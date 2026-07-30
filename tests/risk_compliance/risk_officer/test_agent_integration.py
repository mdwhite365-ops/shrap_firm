"""The graduated agent: gate ordering, scale-down, and the stream contract.

The contract this card must not break is the one every other agent depends on:
``trading.decision.intent`` in, ``risk.intent.approved`` / ``risk.intent.vetoed``
out. The Decision Maker, Execution Agent and Audit Logger were not touched, so
if the shape of those events changed, they break silently.
"""

from __future__ import annotations

from typing import Any

from shrap.events import Envelope, EventPublisher, ReceivedEvent
from shrap.risk_compliance.pre_trade import RiskPolicy
from shrap.risk_compliance.pre_trade_checker_agent import (
    STREAM_RISK_APPROVED,
    STREAM_RISK_VETOED,
    _scale_down,
    latest_regime,
    process_intent_event,
)
from shrap.risk_compliance.risk_officer.officer import RiskAssessment

POLICY = RiskPolicy(allowed_universe={"AAPL"}, max_quantity_per_order=100)


class FakeRedis:
    def __init__(self, entries: list[Any] | None = None, fail: bool = False) -> None:
        self.published: list[tuple[str, dict[str, str]]] = []
        self._entries = entries or []
        self._fail = fail

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.published.append((stream, fields))
        return "1-1"

    async def xrevrange(self, stream: str, count: int = 1) -> list[Any]:
        if self._fail:
            raise RuntimeError("redis down")
        return self._entries


class FakeOfficer:
    """Returns a canned assessment and records what it was asked."""

    def __init__(self, assessment: RiskAssessment) -> None:
        self._assessment = assessment
        self.calls: list[dict[str, Any]] = []
        self.store = _FakeRiskStore()

    async def assess(self, **kwargs: Any) -> RiskAssessment:
        self.calls.append(kwargs)
        return self._assessment


class _FakeRiskStore:
    def __init__(self) -> None:
        self.rows: list[Any] = []

    async def record_decision(self, row: Any) -> None:
        self.rows.append(row)


async def _intent_event(
    quantity: int = 40, side: str = "buy", ticker: str = "AAPL"
) -> ReceivedEvent:
    """Build a real intent event by publishing it, so the envelope is genuine
    rather than a hand-assembled approximation of one."""

    sink = FakeRedis()
    await EventPublisher(sink).publish(  # type: ignore[arg-type]
        stream="trading.decision.intent",
        produced_by="trading/decision-maker",
        schema_version="1.0.0",
        payload={
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "mode": "paper",
            "strategy_ids": ["S1"],
        },
    )
    return ReceivedEvent(
        stream="trading.decision.intent",
        redis_stream_id="1-1",
        envelope=Envelope.from_redis_fields(sink.published[0][1]),
    )


async def _regime_event(payload: dict[str, Any]) -> list[Any]:
    sink = FakeRedis()
    await EventPublisher(sink).publish(  # type: ignore[arg-type]
        stream="intel.regime.sizing-modifier",
        produced_by="intel/regime-classifier",
        schema_version="1.0.0",
        payload=payload,
    )
    return [("1-1", sink.published[0][1])]


def _payload(redis: FakeRedis) -> dict[str, Any]:
    _, fields = redis.published[-1]
    return Envelope.from_redis_fields(fields).payload or {}


# --- the stream contract ------------------------------------------------------


async def test_an_approved_intent_still_lands_on_the_approved_stream() -> None:
    redis = FakeRedis()
    officer = FakeOfficer(
        RiskAssessment(approved=True, approved_quantity=40, reason_code="APPROVED")
    )

    await process_intent_event(redis, await _intent_event(), POLICY, officer=officer)  # type: ignore[arg-type]

    assert redis.published[-1][0] == STREAM_RISK_APPROVED


async def test_a_portfolio_veto_lands_on_the_vetoed_stream() -> None:
    redis = FakeRedis()
    officer = FakeOfficer(
        RiskAssessment(
            approved=False,
            approved_quantity=0,
            reason_code="EXCEEDS_CLUSTER_CAP",
            notes=["too concentrated"],
        )
    )

    await process_intent_event(redis, await _intent_event(), POLICY, officer=officer)  # type: ignore[arg-type]

    assert redis.published[-1][0] == STREAM_RISK_VETOED
    payload = _payload(redis)
    assert payload["approved"] is False
    assert payload["reason_code"] == "EXCEEDS_CLUSTER_CAP"
    assert "approved_intent_payload" not in payload


async def test_the_agent_runs_unchanged_without_an_officer() -> None:
    """Portfolio enforcement is opt-in. With it off the service behaves exactly
    as the Pre-Trade Checker did."""

    redis = FakeRedis()

    await process_intent_event(redis, await _intent_event(), POLICY)  # type: ignore[arg-type]

    payload = _payload(redis)
    assert payload["approved"] is True
    assert payload["approved_quantity"] == 40
    assert "portfolio" not in payload


# --- scale down, don't reject -------------------------------------------------


async def test_a_scaled_intent_stays_approved_at_the_smaller_size() -> None:
    redis = FakeRedis()
    officer = FakeOfficer(
        RiskAssessment(
            approved=True,
            approved_quantity=15,
            reason_code="SCALED_DOWN_PORTFOLIO_LIMIT",
            notes=["scaled 40 -> 15"],
            binding_limit="EXCEEDS_CLUSTER_CAP",
        )
    )

    await process_intent_event(redis, await _intent_event(quantity=40), POLICY, officer=officer)  # type: ignore[arg-type]

    assert redis.published[-1][0] == STREAM_RISK_APPROVED
    payload = _payload(redis)
    assert payload["approved_quantity"] == 15


async def test_scaling_rewrites_the_payload_the_broker_actually_receives() -> None:
    """The bug this guards: updating the reported quantity but not the nested
    approved_intent_payload would send the original size to the broker while
    reporting the reduced one."""

    payload: dict[str, Any] = {
        "approved": True,
        "approved_quantity": 40,
        "reasons": [],
        "approved_intent_payload": {"ticker": "AAPL", "quantity": 40},
    }

    _scale_down(payload, 15, "SCALED_DOWN_PORTFOLIO_LIMIT", "note")

    assert payload["approved_quantity"] == 15
    assert payload["approved_intent_payload"]["quantity"] == 15


# --- gate ordering ------------------------------------------------------------


async def test_the_portfolio_gate_never_runs_on_an_already_vetoed_intent() -> None:
    """It is the most expensive gate — book, equity curve and price history.
    An off-universe ticker must not pay for that."""

    redis = FakeRedis()
    officer = FakeOfficer(
        RiskAssessment(approved=True, approved_quantity=10, reason_code="APPROVED")
    )
    event = await _intent_event(ticker="NOTINUNIVERSE")

    await process_intent_event(redis, event, POLICY, officer=officer)  # type: ignore[arg-type]

    assert officer.calls == []
    assert redis.published[-1][0] == STREAM_RISK_VETOED


async def test_the_portfolio_gate_receives_the_already_clamped_quantity() -> None:
    """The per-order cap runs first, so the portfolio layer sizes against what
    survived it rather than the original request."""

    redis = FakeRedis()
    officer = FakeOfficer(
        RiskAssessment(approved=True, approved_quantity=100, reason_code="APPROVED")
    )

    await process_intent_event(redis, await _intent_event(quantity=500), POLICY, officer=officer)  # type: ignore[arg-type]

    assert officer.calls[0]["quantity"] == 100  # clamped by max_quantity_per_order


# --- the audit row ------------------------------------------------------------


async def test_every_decision_is_recorded() -> None:
    redis = FakeRedis()
    officer = FakeOfficer(
        RiskAssessment(
            approved=False,
            approved_quantity=0,
            reason_code="EXCEEDS_TICKER_CAP",
            account_id="PA1",
            binding_limit="EXCEEDS_TICKER_CAP",
        )
    )

    event = await _intent_event()
    await process_intent_event(redis, event, POLICY, officer=officer)  # type: ignore[arg-type]

    assert len(officer.store.rows) == 1
    row = officer.store.rows[0]
    assert row.approved is False
    assert row.binding_limit == "EXCEEDS_TICKER_CAP"
    assert row.account_id == "PA1"
    # The audit row points back at the intent that caused it.
    assert row.intent_event_id == event.envelope.event_id


async def test_a_failed_audit_write_does_not_block_the_order_path() -> None:
    """The decision is already published and the Execution Agent acts on the
    event, not on this row. Raising here would turn a full audit trail into a
    trading outage."""

    class BrokenStore:
        async def record_decision(self, row: Any) -> None:
            raise RuntimeError("postgres down")

    redis = FakeRedis()
    officer = FakeOfficer(
        RiskAssessment(approved=True, approved_quantity=40, reason_code="APPROVED")
    )
    officer.store = BrokenStore()  # type: ignore[assignment]

    await process_intent_event(redis, await _intent_event(), POLICY, officer=officer)  # type: ignore[arg-type]

    assert redis.published[-1][0] == STREAM_RISK_APPROVED


# --- regime reading -----------------------------------------------------------


async def test_no_regime_event_reads_as_unknown() -> None:
    assert await latest_regime(FakeRedis(entries=[])) == (None, None)


async def test_an_unreachable_regime_stream_reads_as_unknown() -> None:
    """Which resolves to quarter size, not full size."""

    assert await latest_regime(FakeRedis(fail=True)) == (None, None)


async def test_the_label_and_band_are_read_from_the_newest_event() -> None:
    entries = await _regime_event({"label": "wartime", "band": [0.25, 0.75], "confidence": 0.8})

    assert await latest_regime(FakeRedis(entries=entries)) == ("wartime", (0.25, 0.75))


async def test_a_malformed_band_falls_back_to_the_label() -> None:
    entries = await _regime_event({"label": "wartime", "band": "not-a-band"})

    assert await latest_regime(FakeRedis(entries=entries)) == ("wartime", None)
