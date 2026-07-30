"""The book, and the grouping of names that are really one trade.

The failure this whole layer exists to prevent has a specific shape: a book that
passes every per-name limit and is one bet. The clustering is the defence, and
its most important property is what it does when it *cannot* measure — see
`test_unknown_correlation_is_treated_as_correlated`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from shrap.risk_compliance.risk_officer.clusters import (
    breaching_cluster,
    cluster_positions,
    correlation,
    returns,
)
from shrap.risk_compliance.risk_officer.exposure import (
    BookExposure,
    ExposureUnavailable,
    Position,
    build_book,
)

NOW = datetime(2026, 7, 29, 15, 0, tzinfo=UTC)


def _pos(ticker: str, market_value: float) -> Position:
    return Position(ticker=ticker, quantity=0.0, market_value=market_value)


# --- the book -----------------------------------------------------------------


def test_weights_gross_and_net_on_a_long_book() -> None:
    book = BookExposure(nav=10_000.0, positions=(_pos("AAPL", 2_000.0), _pos("MSFT", 1_000.0)))

    assert book.weight("AAPL") == 0.2
    assert book.gross == 0.3
    assert book.net == 0.3


def test_a_short_reduces_net_but_adds_to_gross() -> None:
    """The distinction that makes a dollar-neutral book expressible: gross 100%,
    net 0%. Netting them into one number would let a large hedged book look
    like no book at all."""

    book = BookExposure(nav=10_000.0, positions=(_pos("AAPL", 5_000.0), _pos("MSFT", -5_000.0)))

    assert book.gross == 1.0
    assert book.net == 0.0


def test_duplicate_rows_for_one_ticker_are_summed() -> None:
    book = BookExposure(nav=10_000.0, positions=(_pos("AAPL", 500.0), _pos("aapl", 500.0)))

    assert book.weight("AAPL") == 0.1


def test_projection_adds_a_new_name() -> None:
    book = BookExposure(nav=10_000.0, positions=())

    assert book.projected("AAPL", 1_000.0).weight("AAPL") == 0.1


def test_projection_that_closes_a_position_removes_it() -> None:
    book = BookExposure(nav=10_000.0, positions=(_pos("AAPL", 1_000.0),))

    projected = book.projected("AAPL", -1_000.0)

    assert projected.weight("AAPL") == 0.0
    assert projected.gross == 0.0


def test_a_book_with_no_nav_cannot_carry_exposure() -> None:
    with pytest.raises(ExposureUnavailable):
        BookExposure(nav=0.0, positions=())


# --- freshness ----------------------------------------------------------------


def test_a_flat_account_is_a_real_book() -> None:
    """Empty positions WITH a timestamp is an account holding nothing, which is
    tradeable. Emptiness is never the failure signal."""

    book = build_book(10_000.0, [], NOW - timedelta(minutes=2), NOW)

    assert book.gross == 0.0


def test_never_measured_is_refused_rather_than_treated_as_flat() -> None:
    """The distinction the whole flat-marker row exists to preserve."""

    with pytest.raises(ExposureUnavailable, match="never run"):
        build_book(10_000.0, [], None, NOW)


def test_a_stale_snapshot_is_refused() -> None:
    with pytest.raises(ExposureUnavailable, match="stale"):
        build_book(10_000.0, [], NOW - timedelta(hours=3), NOW)


# --- correlation --------------------------------------------------------------


def test_returns_skip_a_non_positive_price_rather_than_inventing_one() -> None:
    assert returns([100.0, 110.0, 0.0, 120.0]) == pytest.approx([0.1])


def test_a_constant_series_has_no_defined_correlation() -> None:
    """Zero variance is undefined, not zero. Returning 0.0 would claim
    independence on no evidence."""

    assert correlation([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None


def test_identical_movement_correlates_at_one() -> None:
    assert correlation([0.01, -0.02, 0.03], [0.01, -0.02, 0.03]) == pytest.approx(1.0)


def test_too_few_observations_is_none() -> None:
    assert correlation([0.01], [0.02]) is None


# --- clustering ---------------------------------------------------------------


def _lockstep(n: int = 120) -> list[float]:
    return [100.0 * (1.01**i) for i in range(n)]


def _zigzag(period: int, n: int = 120) -> list[float]:
    series = [100.0]
    for step in range(n):
        series.append(series[-1] * (1 + (0.01 if (step // period) % 2 == 0 else -0.01)))
    return series


def test_names_that_move_together_form_one_cluster() -> None:
    together = _lockstep()
    clusters = cluster_positions(
        {"A": 0.08, "B": 0.08, "C": 0.08},
        {"A": together, "B": together, "C": together},
        threshold=0.8,
        min_history=40,
    )

    assert len(clusters) == 1
    assert clusters[0].tickers == ("A", "B", "C")
    assert clusters[0].weight == pytest.approx(0.24)


def test_independent_names_stay_separate() -> None:
    clusters = cluster_positions(
        {"A": 0.08, "B": 0.08},
        {"A": _zigzag(2), "B": _zigzag(7)},
        threshold=0.8,
        min_history=40,
    )

    assert len(clusters) == 2
    assert all(c.is_singleton for c in clusters)


def test_unknown_correlation_is_treated_as_correlated() -> None:
    """The single most important property in this module.

    Too little history resolves to "assume they move together". Assuming
    independence is exactly the error the cluster rule exists to refuse — the
    cost of being wrong here is a cluster that is too big, and the cost of the
    other error is the disaster the rule is named after.
    """

    clusters = cluster_positions(
        {"A": 0.08, "B": 0.08},
        {"A": _zigzag(2), "B": [100.0, 101.0]},  # B has almost no history
        threshold=0.8,
        min_history=40,
    )

    assert len(clusters) == 1
    assert clusters[0].tickers == ("A", "B")


def test_a_name_with_no_history_at_all_clusters_defensively() -> None:
    clusters = cluster_positions(
        {"A": 0.08, "B": 0.08},
        {"A": _zigzag(2)},  # B absent entirely
        threshold=0.8,
        min_history=40,
    )

    assert len(clusters) == 1


def test_clustering_is_transitive() -> None:
    """Single linkage: A-B correlated and B-C correlated puts A, B and C in one
    cluster even if A and C are not directly correlated. The pessimistic choice,
    deliberately — it merges aggressively so the cap binds sooner."""

    together = _lockstep()
    clusters = cluster_positions(
        {"A": 0.05, "B": 0.05, "C": 0.05},
        {"A": together, "B": together, "C": together},
        threshold=0.8,
        min_history=40,
    )

    assert len(clusters) == 1


def test_a_long_and_a_short_do_not_net_within_a_cluster() -> None:
    """Netting would let a book claim zero cluster exposure while holding two
    large offsetting positions whose correlation is an estimate that can break."""

    together = _lockstep()
    clusters = cluster_positions(
        {"A": 0.10, "B": -0.10},
        {"A": together, "B": together},
        threshold=0.8,
        min_history=40,
    )

    assert clusters[0].weight == pytest.approx(0.20)


def test_names_not_held_are_not_clustered() -> None:
    clusters = cluster_positions(
        {"A": 0.08, "B": 0.0},
        {"A": _zigzag(2), "B": _zigzag(2)},
        threshold=0.8,
        min_history=40,
    )

    assert clusters[0].tickers == ("A",)


def test_the_breaching_cluster_is_the_largest_one_over_the_cap() -> None:
    together = _lockstep()
    clusters = cluster_positions(
        {"A": 0.10, "B": 0.10},
        {"A": together, "B": together},
        threshold=0.8,
        min_history=40,
    )

    assert breaching_cluster(clusters, 0.15) is not None
    assert breaching_cluster(clusters, 0.25) is None
