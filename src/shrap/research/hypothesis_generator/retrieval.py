"""What else the firm has read about this effect.

The corpus index (#250) holds ~107,000 chunks of the firm's own filing and paper
text with measured 86.7% precision@5, and until now **nothing read it**. The CLI
queried it; no agent did. This is the first consumer.

**What it changes, stated narrowly.** The proposer has always seen exactly one
abstract and nothing else. It now also sees the passages from the firm's corpus
that are nearest that abstract, so it can tell an isolated claim from a
corroborated one, and say which is which in a field a person can check.

**What it does not change.** Research throughput. The generator is starved
because `research.literature_items` holds nine rows (KI-009, KI-035), and
retrieval over the corpus makes each of those nine better grounded without
producing a tenth. Anyone reading this as a fix for the binding constraint is
reading it wrong.

**The attribution hazard, and why the prior is left alone.** A proposal must name
an author and a year, and refuses without one. If retrieved passages could reach
that field, the model would have a pile of other people's papers to attribute a
claim to, and the citation would look exactly as well-formed as a true one.
So retrieved context is labelled as *other items the firm holds*, the prompt says
in terms that the prior must come from the item under consideration, and
:func:`parse_proposal` still reads `prior` from the item's own metadata. The
retrieval can inform the judgement; it cannot become the citation.

Failure is silent by design: no retriever, an unreachable Qdrant or a slow
embed all yield *no context*, which is the behaviour the generator had before
this module existed. An enrichment that can stop a proposal is worse than no
enrichment.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

DEFAULT_RELATED_LIMIT = 5
"""Top-5, because 86.7% precision@5 is the number the index was measured at.

Asking for more would reach past the part of the ranking anyone has evidence
about — the eval scored the first five hits and says nothing about the sixth.
"""

MAX_PASSAGE_CHARS = 700
"""Enough to judge relevance, short enough that five of them do not crowd out the
abstract they are supposed to be context for."""


@dataclass(frozen=True, slots=True)
class RelatedPassage:
    """One retrieved chunk, with everything needed to distrust it."""

    title: str
    source: str
    text: str
    score: float
    body_kind: str = "full-text"
    """``full-text`` or ``abstract``.

    Carried from the index (#250) and shown to the model, because a hit on an
    abstract is not a hit on the paper. Without it a retrieved advertisement
    reads as a retrieved result.
    """

    url: str = ""

    def render(self) -> str:
        kind = "abstract only" if self.body_kind == "abstract" else "full text"
        passage = " ".join(self.text.split())[:MAX_PASSAGE_CHARS]
        return f"- [{self.source}, {kind}, score {self.score:.2f}] {self.title}\n  {passage}"


class _Embedder(Protocol):
    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class _SearchHit(Protocol):
    @property
    def score(self) -> float: ...

    @property
    def payload(self) -> Mapping[str, Any]: ...


class _VectorSearch(Protocol):
    async def search(self, vector: Sequence[float], *, limit: int) -> Sequence[_SearchHit]: ...


class CorpusRetriever(Protocol):
    """The one call the generator makes against the index."""

    async def related(self, text: str, limit: int) -> Sequence[RelatedPassage]: ...


def render_related(passages: Sequence[RelatedPassage]) -> str:
    """The prompt block, or empty when there is nothing to say.

    Empty rather than a "no results" line: a model told the firm holds nothing
    related may treat that as evidence the effect is novel, which is a claim the
    retrieval cannot support. 287 q-fin papers is a small corpus and absence in
    it means very little.
    """

    if not passages:
        return ""
    body = "\n".join(p.render() for p in passages)
    return (
        "\nOther items the firm already holds, nearest this one by meaning. "
        "They are CONTEXT, not the reference for this proposal — the `prior` "
        "must name the authors of the item above, never one of these:\n"
        f"{body}\n"
    )


async def related_or_nothing(
    retriever: CorpusRetriever | None,
    text: str,
    *,
    limit: int = DEFAULT_RELATED_LIMIT,
) -> tuple[RelatedPassage, ...]:
    """Retrieve, or return nothing and carry on.

    Every failure mode — no retriever configured, Qdrant unreachable, the
    embedding model gone — lands here as an empty tuple, because the generator
    worked without this for its whole life and an optional enrichment must not
    be able to stop a proposal.
    """

    if retriever is None or not text.strip():
        return ()
    try:
        found = await retriever.related(text, limit)
    except Exception:
        log.warning("hypothesis_generator.retrieval_failed", exc_info=True)
        return ()
    return tuple(found)


class QdrantCorpusRetriever:
    """:class:`CorpusRetriever` over the corpus index built in #250.

    Embeds the query with the same local model the index was written with —
    ``nomic-embed-text`` — because a vector collection can only be searched by
    the model that filled it. Searching a 768-dimension cosine index with
    anything else returns well-formed nonsense rather than an error.
    """

    def __init__(self, embedder: _Embedder, qdrant: _VectorSearch) -> None:
        self._embedder = embedder
        self._qdrant = qdrant

    async def related(self, text: str, limit: int) -> Sequence[RelatedPassage]:
        vectors = await self._embedder.embed([text])
        if not vectors:
            return ()
        hits = await self._qdrant.search(vectors[0], limit=limit)
        out: list[RelatedPassage] = []
        for hit in hits:
            payload = hit.payload or {}
            out.append(
                RelatedPassage(
                    title=str(payload.get("title") or payload.get("ref") or "untitled"),
                    source=str(
                        payload.get("feed") or payload.get("symbol") or payload.get("source") or "?"
                    ),
                    text=str(payload.get("text") or ""),
                    score=float(hit.score),
                    body_kind=str(payload.get("body_kind") or "full-text"),
                    url=str(payload.get("url") or ""),
                )
            )
        return tuple(out)


__all__ = [
    "DEFAULT_RELATED_LIMIT",
    "MAX_PASSAGE_CHARS",
    "CorpusRetriever",
    "QdrantCorpusRetriever",
    "RelatedPassage",
    "related_or_nothing",
    "render_related",
]
