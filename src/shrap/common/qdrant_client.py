"""Thin Qdrant HTTP API wrapper.

Same shape and same reasoning as :mod:`shrap.common.prom_client`: a handful of
calls the firm actually makes, over ``httpx``, rather than a dependency. The
official ``qdrant-client`` is good and would be the right call if the firm used
more of Qdrant than "put vectors in, get nearest neighbours out" — it does not,
and "boring beats clever" is an operating principle. Four endpoints is less
surface than a pinned client library.

**Qdrant held nothing at all until this module existed.** It was deployed
2026-07-02, ran healthy for two and a half months, was listed in CLAUDE.md under
"In production now", and had zero collections the entire time. The architecture
(``docs/02-architecture.md``) specifies "full text to Qdrant" for the
Intelligence and Structural Analysis departments; that leg was never wired.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

# Qdrant's own default. Named rather than inlined because the collection name is
# also a migration boundary: changing the embedding model changes the vector
# dimension, and a collection cannot change dimension in place.
DEFAULT_COLLECTION = "shrap_corpus"

# Cosine, because `nomic-embed-text` produces vectors intended to be compared by
# angle rather than magnitude. Getting this wrong does not error — it silently
# ranks by the wrong thing, which is the failure mode this project keeps meeting.
DEFAULT_DISTANCE = "Cosine"

# **Points per HTTP request, and this number was learned the hard way.** The
# first real indexing run died with `httpx.WriteTimeout` after 65 documents: the
# indexer batches by DOCUMENT, and twenty 400 KB filings produce ~2,000 chunks,
# each carrying a 768-float vector plus 2,000 characters of text. That is a
# ~34 MB JSON body in a single PUT, which no sane timeout accommodates.
#
# Bounding the request is the fix; raising the timeout would only have made the
# failure slower. 256 points is roughly 4 MB — large enough that the round trips
# are not the bottleneck, small enough that one is never in doubt.
DEFAULT_UPSERT_BATCH = 256

# Writes are bulk and slow; reads are small. One timeout for both would either
# make a search hang on a dead backend or make a legitimate bulk write fail.
DEFAULT_WRITE_TIMEOUT = 120.0


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One nearest neighbour, with the payload that says what it is.

    A bare score and an id would make retrieval unauditable: the point of
    storing provenance on every point is that a retrieved passage can be traced
    to the filing or paper it came from.
    """

    point_id: str
    score: float
    payload: Mapping[str, Any]


class QdrantClient:
    """The four calls the corpus index makes."""

    def __init__(
        self,
        base_url: str = "http://qdrant:6333",
        timeout: float = 30.0,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._write_timeout = write_timeout

    async def collection_exists(self, collection: str = DEFAULT_COLLECTION) -> bool:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self._base_url}/collections/{collection}")
        return resp.status_code == 200

    async def ensure_collection(
        self,
        collection: str = DEFAULT_COLLECTION,
        *,
        dimension: int,
        distance: str = DEFAULT_DISTANCE,
    ) -> bool:
        """Create the collection if absent. Returns True when it created one.

        **Never recreates an existing collection**, even if the dimension
        disagrees. Dropping and rebuilding would silently discard an index that
        took an hour to build; a dimension mismatch is a migration the operator
        should decide about, and :meth:`dimension_of` is how a caller checks.
        """

        if await self.collection_exists(collection):
            return False
        body = {"vectors": {"size": dimension, "distance": distance}}
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.put(f"{self._base_url}/collections/{collection}", json=body)
            resp.raise_for_status()
        return True

    async def dimension_of(self, collection: str = DEFAULT_COLLECTION) -> int | None:
        """The stored vector size, or None if the collection does not exist.

        Exists so the indexer can refuse to write 768-dimensional vectors into a
        collection built for something else. Qdrant rejects that per-request,
        but the useful place to find out is before embedding ten thousand
        chunks.
        """

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(f"{self._base_url}/collections/{collection}")
        if resp.status_code != 200:
            return None
        params = resp.json().get("result", {}).get("config", {}).get("params", {})
        vectors = params.get("vectors")
        if isinstance(vectors, Mapping) and "size" in vectors:
            return int(vectors["size"])
        return None

    async def upsert(
        self,
        points: Sequence[Mapping[str, Any]],
        collection: str = DEFAULT_COLLECTION,
        *,
        wait: bool = True,
        batch_size: int = DEFAULT_UPSERT_BATCH,
    ) -> int:
        """Insert or replace ``points``; returns how many were sent.

        **Split into bounded requests.** The caller decides how many points to
        hand over and has no idea how large they are — a document-batching
        indexer can produce two thousand at once. Sizing the HTTP request is
        this layer's job, not the caller's, because this is the layer that knows
        a request is being made at all.

        ``wait=True`` because the indexer's cursor advances on the strength of
        this call. Returning before the write is durable would let a crash lose
        chunks the cursor has already claimed were stored — the cursor would
        then never revisit them.
        """

        if not points:
            return 0
        sent = 0
        async with httpx.AsyncClient(timeout=self._write_timeout) as client:
            for start in range(0, len(points), batch_size):
                slice_ = list(points[start : start + batch_size])
                resp = await client.put(
                    f"{self._base_url}/collections/{collection}/points",
                    params={"wait": "true" if wait else "false"},
                    json={"points": slice_},
                )
                resp.raise_for_status()
                sent += len(slice_)
        return sent

    async def search(
        self,
        vector: Sequence[float],
        collection: str = DEFAULT_COLLECTION,
        *,
        limit: int = 10,
        query_filter: Mapping[str, Any] | None = None,
    ) -> list[SearchHit]:
        """Nearest neighbours, newest Qdrant query API."""

        body: dict[str, Any] = {
            "query": list(vector),
            "limit": limit,
            "with_payload": True,
        }
        if query_filter:
            body["filter"] = dict(query_filter)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/collections/{collection}/points/query", json=body
            )
            resp.raise_for_status()
            data = resp.json()
        points = data.get("result", {}).get("points", [])
        return [
            SearchHit(
                point_id=str(p.get("id", "")),
                score=float(p.get("score", 0.0)),
                payload=p.get("payload") or {},
            )
            for p in points
        ]

    async def count(self, collection: str = DEFAULT_COLLECTION) -> int:
        """How many points are stored. 0 for a collection that does not exist.

        The one number that answers "is the pipeline actually flowing", which is
        the question nobody could answer for two and a half months.
        """

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._base_url}/collections/{collection}/points/count",
                json={"exact": True},
            )
        if resp.status_code != 200:
            return 0
        return int(resp.json().get("result", {}).get("count", 0))


__all__ = [
    "DEFAULT_COLLECTION",
    "DEFAULT_DISTANCE",
    "QdrantClient",
    "SearchHit",
]
