"""Embeddings from Ollama's ``/api/embed``.

**Local, not cloud, and that is the point.** Vision principle 5 says cloud is
scaffolding; embeddings are the easiest thing in the firm to bring home. The
model is ``nomic-embed-text`` — 274 MB, 768 dimensions, runs on the Dell's CPU,
no per-token cost, and no dependency on a subscription that retires models
(``qwen3.5:397b`` is retired 2026-09-25, which is what forced #231).

Indexing 156 MB of corpus through a paid API would also be the single largest
LLM spend the firm has ever made, for a job a 274 MB local model does.

**The dimension is a fact about the model, not a constant to assert.**
:meth:`OllamaEmbedder.probe_dimension` asks. A collection is created from what
the model actually returned, so swapping models cannot silently write vectors of
the wrong width into an existing index — the shape of bug this project keeps
finding, where a component asserts something the system already knows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import httpx
import structlog

log = structlog.get_logger(__name__)

DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_URL = "http://ollama:11434"

# Ollama's /api/embed takes a list and embeds it in one pass. 32 keeps the
# request body modest while cutting round trips by the same factor — the
# difference between ~78,000 HTTP calls and ~2,400 for a full corpus pass.
DEFAULT_BATCH_SIZE = 32

# **How many batches are in flight at once, and why this is not 1.**
#
# The first full run held the box at 120% CPU — which is 1.2 of the Dell's
# twelve cores, because `docker stats` reports a percentage of ONE core. Ten
# cores sat idle. The cause was this module: `embed` awaited each batch in turn,
# so there was exactly one request in flight at any instant and Ollama served it
# with its own internal threading and nothing else.
#
# Four rather than twelve. Ollama uses ~1.2 cores per in-flight request, so four
# is ~5 cores — a real speedup while leaving most of the machine to the 39
# containers that are also running, including the trading path. Saturating the
# box to index a backlog faster would be a poor trade.
DEFAULT_CONCURRENCY = 4


class EmbeddingError(RuntimeError):
    """The embedding backend did not return usable vectors."""


class OllamaEmbedder:
    """Batched embeddings, with the vector width read from the model."""

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_URL,
        *,
        model: str = DEFAULT_MODEL,
        timeout: float = 120.0,
        batch_size: int = DEFAULT_BATCH_SIZE,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._batch_size = batch_size
        self._concurrency = max(1, concurrency)

    @property
    def model(self) -> str:
        return self._model

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed every text, in order, batching under the hood.

        Order is part of the contract: the indexer pairs the returned vectors
        with the chunks it sent by position, so a backend that reordered or
        dropped one would attach passages to the wrong documents. The length
        check below is what makes that a loud failure rather than a silent
        mis-attribution.
        """

        if not texts:
            return []

        batches = [
            list(texts[start : start + self._batch_size])
            for start in range(0, len(texts), self._batch_size)
        ]
        limit = asyncio.Semaphore(self._concurrency)

        # One client for the whole call, so concurrent batches reuse connections
        # instead of each paying a fresh handshake.
        async with httpx.AsyncClient(timeout=self._timeout) as client:

            async def run(batch: list[str]) -> list[list[float]]:
                async with limit:
                    return await self._embed_batch(batch, client)

            # `gather` returns results in the order the awaitables were passed,
            # regardless of completion order. That is load-bearing: the indexer
            # pairs vectors to chunks by position, so a reordering here would
            # attach passages to the wrong documents — silently, and in a way no
            # count or checksum would reveal.
            results = await asyncio.gather(*(run(b) for b in batches))

        out: list[list[float]] = [vector for batch_result in results for vector in batch_result]
        if len(out) != len(texts):
            raise EmbeddingError(f"asked for {len(texts)} embeddings, got {len(out)}")
        return out

    async def _embed_batch(
        self, batch: Sequence[str], client: httpx.AsyncClient
    ) -> list[list[float]]:
        resp = await client.post(
            f"{self._base_url}/api/embed",
            json={"model": self._model, "input": list(batch)},
        )
        resp.raise_for_status()
        data = resp.json()
        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(batch):
            raise EmbeddingError(
                f"{self._model} returned {type(vectors).__name__} "
                f"for {len(batch)} inputs; expected a list of {len(batch)} vectors"
            )
        return [[float(x) for x in vec] for vec in vectors]

    async def probe_dimension(self) -> int:
        """Ask the model how wide its vectors are.

        Called once, before the collection is created. Asserting 768 in a
        constant would work until someone changed the model, at which point
        Qdrant would reject every write — or worse, accept them into a
        collection that happened to match and mean something else.
        """

        vectors = await self.embed(["dimension probe"])
        if not vectors or not vectors[0]:
            raise EmbeddingError(f"{self._model} returned no vector for a probe")
        dimension = len(vectors[0])
        log.info("corpus_index.embedder_probed", model=self._model, dimension=dimension)
        return dimension


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_MODEL",
    "DEFAULT_OLLAMA_URL",
    "EmbeddingError",
    "OllamaEmbedder",
]
