# Corpus index — semantic search over the firm's own text

**Built 2026-09-19.** Before this, Qdrant had been running for two and a half
months holding **zero collections**, while 164 MB of filing and paper text sat
in Postgres searchable only by exact string match or by paying an LLM to read
it. `docs/02-architecture.md` specifies *"full text to Qdrant"* for the
Intelligence and Structural Analysis departments; that leg was never wired.

## What is indexed

| source | documents | text | from |
|---|---|---|---|
| `filings` | 169 | 72.0 MB | `intelligence.filings.full_text` (8-K bodies) |
| `research-items` | 15,318 | 91.7 MB | `research.raw_source_items.document_text` (arXiv, EDGAR) |

~82,000 chunks at 2,000 characters with 200 of overlap. Measured throughput is
**~18 chunks/second**, so a full pass is roughly **75 minutes** on the Dell's
CPU.

## Running it

```bash
cd /mnt/Archive/shrap/shrap_firm/infra

docker compose --profile tools run --rm corpus-index \
    shrap-corpus-index build --estimate-only          # size it first

docker compose --profile tools run --rm corpus-index \
    shrap-corpus-index build                          # index from the cursor

docker compose --profile tools run --rm corpus-index \
    shrap-corpus-index search "unusual volume before an earnings announcement"

docker compose --profile tools run --rm corpus-index \
    shrap-corpus-index status                         # points + per-source cursors
```

`build` resumes from `research.corpus_index_cursor` and is **safe to
interrupt**: the cursor advances per batch, after the upsert has returned, and
point ids are a UUIDv5 over `(source, ref, chunk_index)` so a re-run overwrites
rather than duplicates. A long pass can be run detached:

```bash
docker compose --profile tools run -d --name corpus_full corpus-index \
    shrap-corpus-index build
docker logs -f corpus_full
```

## Embeddings are local, on purpose

`nomic-embed-text` (274 MB, 768 dimensions) on the Dell's own Ollama —
**`CORPUS_INDEX_OLLAMA_URL` points at `http://ollama:11434`, not the cloud host
the Tech Watcher's filter uses.** Three reasons, in order:

1. Vision principle 5: cloud is scaffolding. Embeddings are the easiest thing in
   the firm to bring home.
2. 164 MB through a paid API would be the single largest LLM spend the firm has
   ever made, for a job a 274 MB local model does on CPU.
3. No exposure to model retirement. `qwen3.5:397b` is retired 2026-09-25, which
   is what forced #231; a local model is retired when we say so.

## The dimension is asked, never asserted

The collection is created from whatever the model actually returns. If the
model ever changes, the indexer **refuses** rather than writing vectors of the
wrong width:

```
collection 'shrap_corpus' stores 768-dim vectors but <model> produces 1024-dim.
Embedding model changed: index into a new collection, or drop this one
deliberately — it cannot be migrated in place.
```

A vector collection cannot change dimension in place. Changing the embedding
model means rebuilding the index, and that is a decision, not a side effect.

## Verifying it actually works

An index nobody has queried is indistinguishable from an empty one — which is
precisely the state Qdrant sat in while reporting healthy. **`search` is the
check**, not `status`:

```
query: "material definitive agreement and debt financing"

1. [0.5507] LMT — 8-K - LOCKHEED MARTIN CORP
   https://www.sec.gov/Archives/edgar/data/936468/...
   chunk 12: ...the company's reliance on contracts with the U.S. Government...
```

Every hit carries the ticker or feed, the source URL, the chunk position and
the passage text, so a result can be traced to a row someone can go and read.

## What this does not do yet

- **No agent consumes it.** The index exists and is queryable by CLI; nothing in
  the Research funnel retrieves from it. Wiring the Hypothesis Generator to
  retrieve related prior work is the obvious next card and is **not** in this
  one.
- **No scheduled refresh.** New filings and papers are indexed only when `build`
  is run. A trigger service on the Filing Processor's cadence is a follow-up.
- **`intelligence.news_items` is not indexed** (2,659 rows). It has no
  full-text column of the same shape; adding it means deciding what "the
  document" is for a news item.

## This does not address the binding constraint

Worth stating plainly so nobody reads the index as a fix for research
throughput. The funnel is starved because the **filter admits nothing** —
`kimi-k3` has scored 172 items and returned 0 relevant, five model families
deep, and `research.literature_items` holds 9 rows (KI-009, KI-035). Semantic
search over the corpus the filter *rejected* is a different capability: it makes
that corpus reachable, which is useful and is not the same as fixing the
taxonomy.
