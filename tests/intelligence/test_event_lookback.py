"""The look-back (ADR-0018 card 1): big moves, and what was on file before them.

Most of these pin a distinction the report must not blur: a move before the
corpus existed is not an unexplained move, an impossible return is not an
event, and an item published after the close did not cause that day's move.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta

import pytest

from shrap.intelligence.event_lookback import (
    CATEGORY_UNSCORED,
    CAUSE_BEFORE_CORPUS,
    CAUSE_MARKET_WIDE,
    CAUSE_UNEXPLAINED,
    KIND_FILING,
    KIND_NEWS,
    MARKET_TZ,
    Close,
    Evidence,
    Move,
    attribute,
    evidence_for,
    find_moves,
    market_wide_days,
    render_report,
    summarise,
)
from shrap.intelligence.event_lookback_cli import (
    attribution_start,
    filing_evidence,
    news_evidence,
)

START = date(2026, 3, 2)  # a Monday


def _sessions(n: int, start: date = START) -> list[date]:
    """``n`` weekdays from ``start``."""

    out: list[date] = []
    day = start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _series(returns: list[float], start: date = START, first: float = 100.0) -> list[Close]:
    """Closes that realise ``returns`` exactly, one per weekday."""

    days = _sessions(len(returns) + 1, start)
    closes = [Close(days[0], first)]
    for day, ret in zip(days[1:], returns, strict=True):
        closes.append(Close(day, closes[-1].close * (1.0 + ret)))
    return closes


def _quiet(n: int) -> list[float]:
    """Alternating +/-1%: a 1% daily vol with no drift."""

    return [0.01 if i % 2 == 0 else -0.01 for i in range(n)]


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=MARKET_TZ)


def _move(ticker: str, day: date, ret: float = -0.08, prior: date | None = None) -> Move:
    return Move(ticker, day, prior or day - timedelta(days=1), ret, 0.01)


def _news(ticker: str, at: datetime, category: str = "earnings", materiality: int = 3) -> Evidence:
    return Evidence(KIND_NEWS, ticker, at, category, materiality, f"{ticker} {category}")


# --- finding moves --------------------------------------------------------------


def test_a_six_percent_day_in_a_one_percent_name_is_a_move() -> None:
    closes = {"NVDA": _series([*_quiet(40), -0.06, *_quiet(5)])}

    moves, suspect = find_moves(closes)

    assert [(m.ticker, round(m.ret, 4)) for m in moves] == [("NVDA", -0.06)]
    assert moves[0].z == pytest.approx(6.0, rel=0.05)
    assert suspect == []


def test_a_quiet_series_has_no_moves() -> None:
    assert find_moves({"SPY": _series(_quiet(80))}) == ([], [])


def test_a_move_is_measured_against_the_days_before_it_not_itself() -> None:
    """Included in its own yardstick over a 20-return window, a 6% day in a 1%
    name scores 3.6 sigma. Against the days before it, it is 5.8."""

    closes = {"AMD": _series([*_quiet(20), 0.06])}

    [move], _ = find_moves(closes, vol_window=20)

    assert move.z == pytest.approx(5.85, abs=0.01)


def test_a_three_sigma_day_under_the_absolute_floor_is_not_ranked() -> None:
    """A dollar fund's 0.9% day is unusual for it and is not an event."""

    tiny = [0.001 if i % 2 == 0 else -0.001 for i in range(40)]

    assert find_moves({"UUP": _series([*tiny, 0.009])})[0] == []


def test_too_little_history_is_not_a_yardstick() -> None:
    assert find_moves({"NEW": _series([*_quiet(10), -0.09])})[0] == []


def test_a_198_percent_day_is_a_data_fault_and_does_not_mute_what_follows() -> None:
    """ETHA, #290. Ranked as an event it would top the table. Left in the
    yardstick it would raise sigma to ~29% and hide the real 7% day after it."""

    closes = {"ETHA": _series([*_quiet(40), 1.98, *_quiet(5), -0.07])}

    moves, suspect = find_moves(closes)

    assert [round(m.ret, 2) for m in suspect] == [1.98]
    assert [round(m.ret, 2) for m in moves] == [-0.07]


# --- whose day it was -----------------------------------------------------------


def test_an_index_fund_moving_makes_it_the_markets_day() -> None:
    day = date(2026, 11, 5)

    assert market_wide_days([_move("QQQ", day), _move("NVDA", day)]) == {day}


def test_eight_names_together_make_it_the_markets_day_without_an_index() -> None:
    day = date(2026, 11, 5)
    moves = [_move(t, day) for t in ("A", "B", "C", "D", "E", "F", "G", "H")]

    assert market_wide_days(moves) == {day}
    assert market_wide_days(moves[:7]) == frozenset()


# --- what could have caused it --------------------------------------------------


def test_an_item_after_the_close_did_not_move_that_session() -> None:
    """Earnings at 16:05 move the next session. Joined by calendar day they
    would be filed under a day the name did not move."""

    day = date(2026, 10, 28)
    move = _move("META", day)
    during = _news("META", _at(day, 9, 45))
    after = _news("META", _at(day, 16, 5))

    assert evidence_for(move, [during, after]) == (during,)


def test_the_window_opens_at_the_previous_sessions_close() -> None:
    """A Monday move is explained by Friday evening and the weekend."""

    monday, friday = date(2026, 11, 9), date(2026, 11, 6)
    move = _move("TSLA", monday, prior=friday)
    friday_evening = _news("TSLA", _at(friday, 17, 30))
    friday_morning = _news("TSLA", _at(friday, 10, 0))

    assert evidence_for(move, [friday_evening, friday_morning]) == (friday_evening,)


def test_evidence_about_another_name_is_not_evidence() -> None:
    day = date(2026, 10, 28)

    assert evidence_for(_move("META", day), [_news("GOOGL", _at(day, 10))]) == ()


def test_a_naive_timestamp_is_read_as_utc() -> None:
    day = date(2026, 10, 28)
    item = _news("META", datetime(2026, 10, 28, 15, 0))  # 15:00 UTC = 11:00 ET

    assert evidence_for(_move("META", day), [item]) == (item,)


# --- naming the cause -----------------------------------------------------------

CORPUS_START = datetime(2026, 7, 17, tzinfo=UTC)


def test_a_move_before_the_corpus_is_not_called_unexplained() -> None:
    """Nothing could have been on file. Counted as unexplained, 2022 would make
    the corpus look blind to everything."""

    old = _move("NVDA", date(2022, 5, 18))

    [result] = attribute([old], [], corpus_start=CORPUS_START)

    assert result.cause == CAUSE_BEFORE_CORPUS


def test_with_nothing_on_file_at_all_every_move_is_before_the_corpus() -> None:
    [result] = attribute([_move("NVDA", date(2026, 10, 1))], [], corpus_start=None)

    assert result.cause == CAUSE_BEFORE_CORPUS


def test_the_most_material_informative_item_names_the_cause() -> None:
    day = date(2026, 10, 28)
    minor = _news("META", _at(day, 9), category="product", materiality=1)
    major = _news("META", _at(day, 10), category="guidance", materiality=3)

    [result] = attribute([_move("META", day)], [minor, major], corpus_start=CORPUS_START)

    assert result.cause == "guidance"
    assert result.evidence == (major, minor)


def test_a_results_filing_and_an_earnings_headline_are_one_cause() -> None:
    day = date(2026, 10, 28)
    filing = Evidence(KIND_FILING, "META", _at(day, 8), "results", 2, "8-K")

    [result] = attribute([_move("META", day)], [filing], corpus_start=CORPUS_START)

    assert result.cause == "earnings"


@pytest.mark.parametrize("category", ["other", "other-events", CATEGORY_UNSCORED])
def test_an_item_that_says_nothing_about_why_leaves_the_move_unexplained(category: str) -> None:
    """The item is kept as evidence. It cannot name a cause."""

    day = date(2026, 10, 28)
    item = _news("META", _at(day, 9), category=category, materiality=3)

    [result] = attribute([_move("META", day)], [item], corpus_start=CORPUS_START)

    assert result.cause == CAUSE_UNEXPLAINED
    assert result.evidence == (item,)


def test_on_the_markets_day_one_names_own_filing_is_not_the_cause() -> None:
    day = date(2026, 11, 5)
    moves = [_move("QQQ", day), _move("NVDA", day)]
    coincidence = _news("NVDA", _at(day, 8), category="management", materiality=3)

    results = attribute(moves, [coincidence], corpus_start=CORPUS_START)

    assert {r.cause for r in results} == {CAUSE_MARKET_WIDE}
    assert all(r.market_wide for r in results)


# --- counting -------------------------------------------------------------------


def test_forty_names_falling_on_one_day_is_one_session_not_forty() -> None:
    day = date(2026, 11, 5)
    moves = [_move(f"T{i}", day) for i in range(40)]

    [row] = summarise(attribute(moves, [], corpus_start=CORPUS_START))

    assert (row.cause, row.sessions, row.moves, row.tickers) == (CAUSE_MARKET_WIDE, 1, 40, 40)


def test_causes_are_ranked_by_sessions() -> None:
    d1, d2, d3 = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)
    evidence = [_news("META", _at(d, 9)) for d in (d1, d2)]
    moves = [_move("META", d1), _move("META", d2), _move("NIO", d3)]

    rows = summarise(attribute(moves, evidence, corpus_start=CORPUS_START))

    assert [(r.cause, r.sessions) for r in rows] == [("earnings", 2), (CAUSE_UNEXPLAINED, 1)]


# --- the report -----------------------------------------------------------------


def _report(moves: list[Move], evidence: list[Evidence], suspect: list[Move] | None = None) -> str:
    return render_report(
        attribute(moves, evidence, corpus_start=CORPUS_START),
        suspect or [],
        first_session=date(2021, 1, 4),
        last_session=date(2026, 11, 10),
        corpus_start=CORPUS_START,
        tickers=50,
        source_starts={"news": CORPUS_START, "filings": datetime(2026, 7, 1, tzinfo=UTC)},
    )


def test_the_report_states_the_share_it_could_not_explain() -> None:
    d1, d2 = date(2026, 10, 1), date(2026, 10, 2)
    text = _report([_move("META", d1), _move("NIO", d2)], [_news("META", _at(d1, 9))])

    assert "1 (50%) have no informative item on file" in text
    assert "| earnings | 1 | 1 | 1 |" in text


def test_moves_before_the_corpus_are_counted_and_then_left_out() -> None:
    text = _report([_move("NVDA", date(2022, 5, 18)), _move("META", date(2026, 10, 1))], [])

    assert "1 moves fall before the corpus began" in text
    assert CAUSE_BEFORE_CORPUS not in text
    assert "2022-05-18" not in text


def test_a_report_with_nothing_inside_the_corpus_says_it_found_nothing() -> None:
    text = _report([_move("NVDA", date(2022, 5, 18))], [])

    assert "nothing below is a finding" in text


def test_each_sources_start_is_shown_separately() -> None:
    text = _report([_move("META", date(2026, 10, 1))], [])

    assert "news on file from: 2026-07-17" in text
    assert "filings on file from: 2026-07-01" in text


def test_suspect_returns_are_listed_apart() -> None:
    bad = Move("ETHA", date(2026, 9, 30), date(2026, 9, 29), 1.98, 0.03)
    text = _report([_move("META", date(2026, 10, 1))], [], suspect=[bad])

    assert "Returns too large to believe" in text
    assert "| 2026-09-30 | ETHA | +198.0% |" in text


def test_a_pipe_in_a_headline_does_not_break_the_table() -> None:
    day = date(2026, 10, 1)
    item = Evidence(KIND_NEWS, "META", _at(day, 9), "earnings", 3, "Meta | Q3 results")

    assert "Meta / Q3 results" in _report([_move("META", day)], [item])


# --- reading the rows -----------------------------------------------------------

UNIVERSE = frozenset({"META", "NVDA"})


def test_a_news_item_becomes_evidence_for_each_universe_name_it_carries() -> None:
    row = {
        "published_at": _at(date(2026, 10, 28), 9),
        "headline": "Chip export rules tightened",
        "symbols": ["NVDA", "META", "INTC"],
        "verdict": {"category": "regulatory", "materiality": 3, "summary": "s"},
    }

    found = news_evidence(row, UNIVERSE)

    assert [(e.ticker, e.category, e.materiality) for e in found] == [
        ("NVDA", "regulatory", 3),
        ("META", "regulatory", 3),
    ]


def test_jsonb_arriving_as_text_is_parsed() -> None:
    """asyncpg hands JSONB back as a string unless a codec is registered."""

    row = {
        "published_at": _at(date(2026, 10, 28), 9),
        "headline": "h",
        "symbols": json.dumps(["META"]),
        "verdict": json.dumps({"category": "earnings", "materiality": 2}),
    }

    [item] = news_evidence(row, UNIVERSE)

    assert (item.category, item.materiality) == ("earnings", 2)


def test_an_unscored_news_item_is_kept_and_marked() -> None:
    row = {
        "published_at": _at(date(2026, 10, 28), 9),
        "headline": "h",
        "symbols": ["META"],
        "verdict": None,
    }

    [item] = news_evidence(row, UNIVERSE)

    assert (item.category, item.materiality) == (CATEGORY_UNSCORED, -1)


def test_a_filing_takes_its_most_material_verdict() -> None:
    row = {
        "filing_date": _at(date(2026, 10, 28), 8),
        "symbol": "META",
        "title": "8-K",
        "item_codes": ["2.02", "9.01"],
        "verdicts": [
            {"item_code": "9.01", "category": "other", "materiality": 0},
            {"item_code": "2.02", "category": "results", "materiality": 3},
        ],
    }

    [item] = filing_evidence(row)

    assert (item.cause, item.materiality) == ("earnings", 3)
    assert item.text == "8-K (items 2.02, 9.01)"


def test_an_unscored_results_filing_is_still_earnings() -> None:
    """Item 2.02 is the form's own statement of what it is."""

    row = {
        "filing_date": _at(date(2026, 10, 28), 8),
        "symbol": "META",
        "title": None,
        "item_codes": json.dumps(["2.02"]),
        "verdicts": None,
    }

    [item] = filing_evidence(row)

    assert item.cause == "earnings"


def test_attribution_starts_when_every_source_was_recording() -> None:
    """From the earliest start, a move in the gap would be called unexplained
    when half the corpus could not yet have explained it."""

    news, filings = datetime(2026, 7, 17, tzinfo=UTC), datetime(2026, 8, 2, tzinfo=UTC)

    assert attribution_start({"news": news, "filings": filings}) == filings
    assert attribution_start({"news": news, "filings": None}) is None
    assert attribution_start({}) is None
