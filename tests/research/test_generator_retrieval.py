"""The Hypothesis Generator reads the corpus index — the first agent that does.

#250 built ~107,000 chunks with measured 86.7% precision@5 and nothing consumed
it. The proposer has always seen one abstract and nothing else.

The hazard this is written around is **attribution**. A proposal must name an
author and a year and refuses without one; if retrieved passages could reach
that field, the model would have a pile of other people's papers to attribute
this paper's claim to, and the citation would look exactly as well-formed as a
true one. So retrieval is labelled as context, the prompt says so in terms, and
the `prior` still comes from the item's own metadata.
"""

from __future__ import annotations

from typing import Any

from shrap.research.hypothesis_generator.literature import LiteratureItem
from shrap.research.hypothesis_generator.proposer import build_prompt
from shrap.research.hypothesis_generator.retrieval import (
    RelatedPassage,
    related_or_nothing,
    render_related,
)


def _item() -> LiteratureItem:
    return LiteratureItem(
        item_id="arxiv-qfin:2401.01234v1",
        source="arxiv-qfin",
        category="q-fin.PM",
        title="Illiquidity and the cross-section of expected returns",
        abstract="We document that expected returns rise with illiquidity.",
        url="http://arxiv.org/abs/2401.01234v1",
        published_at=None,
        authors=("Yakov Amihud",),
    )


def _passage(title: str = "A related paper", **kw: Any) -> RelatedPassage:
    base: dict[str, Any] = {
        "title": title,
        "source": "arxiv-qfin",
        "text": "Liquidity premia are larger for small capitalisation names.",
        "score": 0.71,
    }
    base.update(kw)
    return RelatedPassage(**base)


# --- the prompt ---------------------------------------------------------------


def test_with_nothing_retrieved_the_prompt_is_what_it_has_always_been() -> None:
    """An agent with no corpus index must behave exactly as before."""

    assert build_prompt(_item()) == build_prompt(_item(), ())
    assert "already holds" not in build_prompt(_item())


def test_retrieved_context_is_labelled_as_context_not_as_the_reference() -> None:
    prompt = build_prompt(_item(), (_passage(),))

    assert "CONTEXT" in prompt or "context" in prompt.lower()
    # The instruction that stops a fabricated citation.
    assert "must name the authors of the item above" in prompt
    assert "A related paper" in prompt


def test_the_item_comes_first_and_the_context_after() -> None:
    """Order is the other half of the attribution guard: the paper being judged
    is at the top, where the prompt says the prior must come from."""

    prompt = build_prompt(_item(), (_passage(),))

    assert prompt.index("Illiquidity and the cross-section") < prompt.index("A related paper")


def test_an_abstract_hit_is_not_presented_as_a_full_text_hit() -> None:
    """Carried from #250: a retrieved advertisement must not read as a result."""

    assert "abstract only" in render_related((_passage(body_kind="abstract"),))
    assert "full text" in render_related((_passage(body_kind="full-text"),))


def test_a_long_passage_is_clipped_so_five_do_not_bury_the_abstract() -> None:
    long_passage = _passage(text="x" * 5_000)

    rendered = render_related((long_passage,))

    assert len(rendered) < 1_500


def test_nothing_retrieved_renders_nothing_rather_than_a_no_results_line() -> None:
    """A model told the firm holds nothing related may read that as novelty.

    It is not evidence of novelty — the q-fin corpus is 287 papers.
    """

    assert render_related(()) == ""


# --- degradation --------------------------------------------------------------


class _Boom:
    async def related(self, text: str, limit: int) -> list[RelatedPassage]:
        raise RuntimeError("qdrant is down")


class _Works:
    def __init__(self) -> None:
        self.queries: list[tuple[str, int]] = []

    async def related(self, text: str, limit: int) -> list[RelatedPassage]:
        self.queries.append((text, limit))
        return [_passage()]


async def test_no_retriever_configured_yields_no_context() -> None:
    assert await related_or_nothing(None, "anything") == ()


async def test_a_retrieval_failure_does_not_stop_a_proposal() -> None:
    """An optional enrichment must never be able to fail a proposal."""

    assert await related_or_nothing(_Boom(), "anything") == ()


async def test_an_empty_query_is_not_sent() -> None:
    retriever = _Works()

    assert await related_or_nothing(retriever, "   ") == ()
    assert retriever.queries == []


async def test_the_query_carries_the_abstract_not_just_the_title() -> None:
    """A five-word title is a thin query against chunked prose."""

    retriever = _Works()

    await related_or_nothing(retriever, "Some title\nAnd the abstract body")

    text, _limit = retriever.queries[0]
    assert "abstract body" in text
