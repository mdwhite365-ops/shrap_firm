"""``shrap-corpus-index`` — build and query the firm's semantic index.

Two subcommands, and the second is not a convenience. An index nobody has
queried is indistinguishable from an empty one, which is exactly the state
Qdrant sat in for two and a half months while reporting healthy. ``search``
exists so that "the pipeline is flowing" is a claim someone can check in one
command.

    shrap-corpus-index build --max-documents 50
    shrap-corpus-index search "unusual volume before an earnings announcement"
    shrap-corpus-index status
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import cast

from shrap.common.db import create_asyncpg_pool
from shrap.common.qdrant_client import DEFAULT_COLLECTION, QdrantClient
from shrap.intelligence.corpus_index.chunker import estimate_chunks
from shrap.intelligence.corpus_index.embedder import DEFAULT_MODEL, OllamaEmbedder
from shrap.intelligence.corpus_index.indexer import (
    DEFAULT_DOC_BATCH,
    CorpusIndexer,
    IndexerConfig,
)
from shrap.intelligence.corpus_index.store import AsyncPool, CorpusStore

DEFAULT_DSN = "postgresql://shrap:shrap@postgres:5432/shrap"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Chunk, embed and index the firm's stored full text into Qdrant, and query it."
        )
    )
    parser.add_argument(
        "--dsn",
        default=os.environ.get("CORPUS_INDEX_POSTGRES_DSN", DEFAULT_DSN),
        help="Postgres DSN (default: CORPUS_INDEX_POSTGRES_DSN env)",
    )
    parser.add_argument(
        "--qdrant-url",
        default=os.environ.get("CORPUS_INDEX_QDRANT_URL", "http://qdrant:6333"),
        help="Qdrant base URL",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("CORPUS_INDEX_OLLAMA_URL", "http://ollama:11434"),
        help="Ollama base URL (local, not the cloud host — embeddings run here)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Embedding model")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)

    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Index new documents from where the cursor left off")
    build.add_argument(
        "--max-documents",
        type=int,
        default=None,
        help="Stop after N documents (default: drain both sources)",
    )
    build.add_argument("--doc-batch", type=int, default=DEFAULT_DOC_BATCH)
    build.add_argument(
        "--estimate-only",
        action="store_true",
        help="Print the corpus size and chunk estimate, index nothing",
    )

    search = sub.add_parser("search", help="Nearest passages to a query")
    search.add_argument("query", help="Free text")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--source", default=None, help="Restrict to filings|research-items")

    sub.add_parser("status", help="Point count and per-source cursors")
    return parser


async def _run_build(args: argparse.Namespace, pool: AsyncPool) -> str:
    store = CorpusStore(pool)
    await store.ensure_schema()
    sizes = await store.corpus_size()

    lines = ["corpus:"]
    total_chunks = 0
    for source, (documents, chars) in sorted(sizes.items()):
        chunks = estimate_chunks(chars)
        total_chunks += chunks
        lines.append(
            f"  {source:<16} {documents:>6} docs  {chars / 1e6:>7.1f} MB  ~{chunks} chunks"
        )
    lines.append(f"  {'estimated total':<16} {'':>6}       {'':>7}     ~{total_chunks} chunks")

    if args.estimate_only:
        return "\n".join(lines)

    embedder = OllamaEmbedder(args.ollama_url, model=args.model)
    qdrant = QdrantClient(args.qdrant_url)
    indexer = CorpusIndexer(
        store,
        embedder,
        qdrant,
        IndexerConfig(
            collection=args.collection,
            doc_batch=args.doc_batch,
            max_documents=args.max_documents,
        ),
    )
    dimension = await indexer.ensure_ready()
    lines.append(f"\nmodel {args.model} -> {dimension} dims, collection {args.collection}")

    results = await indexer.index_all()
    for source, progress in results.items():
        lines.append(
            f"  {source:<16} indexed {progress.documents} docs -> {progress.chunks} chunks"
            + (f", {progress.skipped_empty} empty" if progress.skipped_empty else "")
        )
    lines.append(f"\ncollection now holds {await qdrant.count(args.collection)} points")
    return "\n".join(lines)


async def _run_search(args: argparse.Namespace) -> str:
    embedder = OllamaEmbedder(args.ollama_url, model=args.model)
    qdrant = QdrantClient(args.qdrant_url)
    vectors = await embedder.embed([args.query])

    query_filter = None
    if args.source:
        query_filter = {"must": [{"key": "source", "match": {"value": args.source}}]}

    hits = await qdrant.search(
        vectors[0], args.collection, limit=args.limit, query_filter=query_filter
    )
    if not hits:
        return "no hits — is the collection built? try `status`"

    lines = [f'query: "{args.query}"', ""]
    for rank, hit in enumerate(hits, start=1):
        payload = hit.payload
        label = payload.get("symbol") or payload.get("feed") or payload.get("source", "?")
        snippet = " ".join(str(payload.get("text", "")).split())[:240]
        lines.append(f"{rank}. [{hit.score:.4f}] {label} — {payload.get('title', '')[:90]}")
        lines.append(f"   {payload.get('url', '')}")
        lines.append(f"   chunk {payload.get('chunk')}: {snippet}...")
        lines.append("")
    return "\n".join(lines)


async def _run_status(args: argparse.Namespace, pool: AsyncPool) -> str:
    store = CorpusStore(pool)
    qdrant = QdrantClient(args.qdrant_url)
    sizes = await store.corpus_size()
    points = await qdrant.count(args.collection)

    lines = [f"collection {args.collection}: {points} points", ""]
    for source, (documents, chars) in sorted(sizes.items()):
        since, ref = await store.cursor_for(source)
        position = f"{since.isoformat()} / {ref[:40]}" if since else "(never indexed)"
        lines.append(f"  {source:<16} {documents:>6} docs  {chars / 1e6:>7.1f} MB")
        lines.append(f"  {'':<16} cursor: {position}")
    return "\n".join(lines)


async def _main(args: argparse.Namespace) -> str:
    if args.command == "search":
        return await _run_search(args)

    pool = cast(AsyncPool, await create_asyncpg_pool(args.dsn))
    try:
        if args.command == "build":
            return await _run_build(args, pool)
        return await _run_status(args, pool)
    finally:
        await pool.close()  # type: ignore[attr-defined]


def main() -> None:
    args = _build_parser().parse_args()
    print(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
