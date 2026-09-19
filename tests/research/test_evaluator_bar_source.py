"""Selecting a feed to backtest against, and recording which one ran.

#245 made the store able to hold IEX and SIP side by side. This is the half
that lets an evaluation *choose*, and — the part that matters for the ledger —
say afterwards which one it chose.

Two runs of one strategy on two feeds produce two rows in
``research.evaluations`` that are identical in every field explaining them:
same strategy_id, same spec_hash, same protocol_version, same folds, same
window. Only the numbers differ. Without ``bar_source`` in the recorded config
a later reader comparing those IRs has no way to know they measured different
markets — and would reasonably conclude the evaluator is nondeterministic.
"""

from __future__ import annotations

from shrap.market_data.client import IEX_FEED, SIP_FEED, source_label
from shrap.market_data.store import DEFAULT_BAR_SOURCE
from shrap.research.strategy_evaluator.cli import _build_parser
from shrap.research.strategy_evaluator.engine import EvalConfig
from shrap.research.strategy_evaluator.store import (
    IntradayEvaluatorReader,
    PostgresEvaluatorReader,
)


def test_sip_feed_label_matches_the_store_convention() -> None:
    """``source_label`` is what the backfill writes; readers must ask for that.

    If these drift, a SIP backfill writes rows under one string and every
    reader asks for another — an empty panel, not an error.
    """

    assert source_label(SIP_FEED) == "alpaca-sip"
    assert source_label(IEX_FEED) == DEFAULT_BAR_SOURCE


def test_cli_defaults_to_iex() -> None:
    """The default must stay the feed already on disk.

    Every stored row is ``alpaca-iex``. A default of anything else turns every
    existing evaluation path into an empty panel on the next run.
    """

    args = _build_parser().parse_args(["--strategy-id", "X"])
    assert args.bar_source == DEFAULT_BAR_SOURCE == "alpaca-iex"


def test_cli_accepts_an_explicit_feed() -> None:
    args = _build_parser().parse_args(["--strategy-id", "X", "--bar-source", "alpaca-sip"])
    assert args.bar_source == "alpaca-sip"


def test_config_records_the_feed_in_what_the_ledger_stores() -> None:
    """``as_dict`` is what lands in ``research.evaluations.config``."""

    recorded = EvalConfig(bar_source="alpaca-sip").as_dict()

    assert recorded["bar_source"] == "alpaca-sip"
    # Beside `adjustment`, the other knob that silently changes the numbers.
    assert recorded["adjustment"] == "all"


def test_config_defaults_to_iex() -> None:
    assert EvalConfig().as_dict()["bar_source"] == DEFAULT_BAR_SOURCE


def test_daily_reader_carries_the_feed_it_was_given() -> None:
    reader = PostgresEvaluatorReader(object(), source="alpaca-sip")  # type: ignore[arg-type]
    assert reader._source == "alpaca-sip"


def test_intraday_reader_passes_the_feed_to_both_halves() -> None:
    """The delegate owns the daily `read_bars`; it must not keep the default.

    A delegate left on IEX while the bar reader ran on SIP would be a second
    opinion about what the market did, inside one evaluation.
    """

    reader = IntradayEvaluatorReader(  # type: ignore[arg-type]
        object(), timeframe="15Min", source="alpaca-sip"
    )

    assert reader.source == "alpaca-sip"
    assert reader._delegate._source == "alpaca-sip"
    assert reader._bars._source == "alpaca-sip"


def test_reader_and_recorded_config_cannot_disagree() -> None:
    """One argument feeds both, so the ledger always describes the panel.

    Mirrors what `cli._run` does. The failure this guards is a run that
    backtests SIP and records IEX — which would not error, would not look
    wrong, and would poison every later comparison.
    """

    args = _build_parser().parse_args(["--strategy-id", "X", "--bar-source", "alpaca-sip"])

    reader = PostgresEvaluatorReader(object(), source=args.bar_source)  # type: ignore[arg-type]
    config = EvalConfig(bar_source=args.bar_source)

    assert reader._source == config.as_dict()["bar_source"]
