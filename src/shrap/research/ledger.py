"""What the firm has tried, what happened, and what it should have learned.

Every evaluation the firm runs produces a verdict, a row in
``research.evaluations``, and a Markdown card. Each of those describes **one
strategy in isolation**, and nothing has ever read across them. The lessons are
all there and none of them accumulate: `shrap-strategy-stage lineage` shows one
idea's history, and the cards sit in a directory nobody re-opens.

That is a problem for a firm whose whole method is "kill more aggressively than
you promote". Killing is only cheap if the corpse teaches you something, and a
lesson that lives in one card that is never re-read has taught nobody.

This module reads the corpus and answers three questions that no per-strategy
view can:

**What died of what.** Five strategies have been killed or held. If four of
those deaths were structural — a dead anchor, too few trades, a refusal — then
the firm has learned almost nothing about *edge*, and believing otherwise is how
a research programme talks itself into a conclusion it never tested.

**Which gate is actually binding.** If nothing has ever cleared the Sharpe floor
while several cleared the information-ratio floor, that is evidence about the
gate, not about the strategies. The opposite pattern is evidence about the
strategies. Only the corpus can tell them apart.

**Whether the kill criteria predicted the kills.** Every strategy declares its
own falsifiers before it runs. A strategy that dies of something it never
predicted is a strategy whose author did not understand it — and that is a fact
about the firm's hypothesis quality, which is the input to everything else.

Deliberately **read-only and derived**. This owns no table and writes nothing;
it is a lens over rows other agents already produce. A ledger that stored its
own summaries could disagree with the evaluations it summarises, and then the
firm would have two answers about its own history.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

# Reasons that describe a defect in the *setup* rather than a finding about the
# strategy's edge. A corpus dominated by these means the firm has been testing
# its own plumbing, however many evaluations it has run.
STRUCTURAL_REASONS: frozenset[str] = frozenset(
    {
        "anchor-not-live",
        "insufficient-trades",
        "insufficient-data",
    }
)

# Reasons that are genuine findings about the strategy itself.
EDGE_REASONS: frozenset[str] = frozenset(
    {
        "no-edge",
        "no-active-edge",
        "fails-friction-stress",
        "below-sharpe-floor",
        "below-information-ratio-floor",
        "below-multiple-testing-adjusted-floor",
        "worse-than-parent",
    }
)


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """One strategy's latest evaluation, flattened for reading."""

    strategy_id: str
    name: str
    status: str
    verdict: str
    reason: str
    protocol_version: str
    total_trades: int
    sharpe: float | None
    information_ratio: float | None
    folds_with_edge: int | None
    n_folds: int | None
    attempts: int
    engine_ran: bool
    created_at: datetime | None
    card_path: str | None

    @property
    def is_structural(self) -> bool:
        """Died of a setup defect rather than a finding about its edge."""

        return self.reason in STRUCTURAL_REASONS or not self.engine_ran

    @property
    def folds(self) -> str:
        if self.n_folds is None:
            return "n/a"
        return f"{self.folds_with_edge}/{self.n_folds}"

    def metric(self, value: float | None) -> str:
        return "n/a" if value is None else f"{value:+.3f}"


@dataclass(frozen=True, slots=True)
class LedgerSummary:
    """What the corpus says, as opposed to what any one row says."""

    total: int
    evaluated: int
    """Rows where the engine actually ran. The rest never reached a backtest."""

    structural_deaths: int
    edge_deaths: int
    promoted: int
    held: int
    killed: int
    cleared_sharpe_floor: int
    cleared_ir_floor: int
    reasons: Mapping[str, int]

    @property
    def learned_about_edge(self) -> int:
        """Evaluations that actually tested a strategy's edge.

        The honest denominator for "how much has the firm learned". An
        evaluation that refused before the backtest, or died on trade count,
        measured the setup rather than the idea.
        """

        return self.edge_deaths + self.promoted

    def lines(self) -> list[str]:
        out = [
            f"strategies      {self.total}",
            f"engine ran      {self.evaluated}",
            f"  edge findings {self.edge_deaths}",
            f"  structural    {self.structural_deaths}",
            f"promoted        {self.promoted}",
            f"held            {self.held}",
            f"killed          {self.killed}",
        ]
        if self.evaluated:
            out.append(f"cleared sharpe  {self.cleared_sharpe_floor}/{self.evaluated}")
            out.append(f"cleared ir      {self.cleared_ir_floor}/{self.evaluated}")
        return out


def summarise(rows: Sequence[LedgerRow], *, sharpe_floor: float, ir_floor: float) -> LedgerSummary:
    """Aggregate the corpus. Pure; no I/O."""

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.reason] = counts.get(row.reason, 0) + 1
    evaluated = [r for r in rows if r.engine_ran]
    return LedgerSummary(
        total=len(rows),
        evaluated=len(evaluated),
        structural_deaths=sum(1 for r in rows if r.is_structural),
        edge_deaths=sum(1 for r in rows if r.engine_ran and r.reason in EDGE_REASONS),
        promoted=sum(1 for r in rows if r.verdict == "promote"),
        held=sum(1 for r in rows if r.verdict == "hold-for-data"),
        killed=sum(1 for r in rows if r.verdict == "kill"),
        cleared_sharpe_floor=sum(
            1 for r in evaluated if r.sharpe is not None and r.sharpe >= sharpe_floor
        ),
        cleared_ir_floor=sum(
            1
            for r in evaluated
            if r.information_ratio is not None and r.information_ratio >= ir_floor
        ),
        reasons=counts,
    )


def observations(summary: LedgerSummary) -> list[str]:
    """Statements the corpus supports, and nothing beyond them.

    Each of these is a conclusion a reader would otherwise have to assemble by
    hand from a directory of cards, which in practice means never. They are
    deliberately conservative: an observation that overstates what a handful of
    evaluations can support would be worse than no observation, because it would
    be *acted on*.
    """

    out: list[str] = []
    if summary.total == 0:
        return ["nothing evaluated yet"]

    if summary.evaluated == 0:
        out.append(
            "no evaluation has reached a backtest — every result so far describes the "
            "setup, not a strategy"
        )
        return out

    if summary.structural_deaths and summary.structural_deaths >= summary.evaluated:
        out.append(
            f"{summary.structural_deaths} of {summary.total} died of setup defects "
            "rather than of anything measured about their edge"
        )

    if summary.learned_about_edge == 0:
        out.append("no strategy has yet been tested on its edge; the corpus is all plumbing")
    else:
        out.append(
            f"{summary.learned_about_edge} of {summary.total} evaluations actually "
            "tested a strategy's edge"
        )

    if summary.promoted == 0 and summary.evaluated:
        out.append(f"nothing has been promoted in {summary.evaluated} completed evaluations")

    # The gate-versus-strategies question. Only meaningful once something has
    # run; a split verdict between the two floors is evidence about the gate.
    if summary.evaluated and summary.cleared_ir_floor and not summary.cleared_sharpe_floor:
        out.append(
            f"{summary.cleared_ir_floor} strategies beat the benchmark by enough but none "
            "cleared the Sharpe floor — that is evidence about the Sharpe gate, since "
            "Sharpe carries the market's return and the information ratio does not"
        )
    if summary.evaluated and summary.cleared_sharpe_floor and not summary.cleared_ir_floor:
        out.append(
            f"{summary.cleared_sharpe_floor} strategies cleared the Sharpe floor while "
            "none beat the benchmark by enough — a warning that the Sharpe floor is "
            "being cleared by market exposure rather than by skill"
        )

    dominant = max(summary.reasons.items(), key=lambda kv: (kv[1], kv[0]), default=None)
    if dominant is not None and dominant[1] > 1:
        out.append(f"most common outcome: {dominant[0]} ({dominant[1]} of {summary.total})")

    return out


class LedgerReader(Protocol):
    async def ledger_rows(self) -> Sequence[Mapping[str, Any]]: ...


def row_from_mapping(raw: Mapping[str, Any]) -> LedgerRow:
    """Build a row from a joined registry/evaluation record.

    Missing metrics stay ``None`` rather than becoming 0.0. A strategy that
    never reached a backtest has no Sharpe, and rendering that as 0.000 would
    put a measurement where there is an absence — which is the specific way a
    summary starts lying.
    """

    aggregate = raw.get("aggregate_metrics") or {}
    active = raw.get("active_metrics") or {}
    consistency = raw.get("consistency_metrics") or {}
    return LedgerRow(
        strategy_id=str(raw["strategy_id"]),
        name=str(raw.get("name") or ""),
        status=str(raw.get("status") or ""),
        verdict=str(raw.get("verdict") or "unevaluated"),
        reason=str(raw.get("reason") or ""),
        protocol_version=str(raw.get("protocol_version") or ""),
        total_trades=int(raw.get("total_trades") or 0),
        sharpe=_optional_float(aggregate.get("sharpe")),
        information_ratio=_optional_float(active.get("information_ratio")),
        folds_with_edge=_optional_int(consistency.get("folds_with_active_edge")),
        n_folds=_optional_int(consistency.get("n_folds")),
        attempts=int(raw.get("attempts") or 1),
        engine_ran=bool(raw.get("verdict")) and int(raw.get("total_trades") or 0) > 0,
        created_at=raw.get("created_at"),
        card_path=_optional_str(raw.get("card_path")),
    )


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def render(rows: Sequence[LedgerRow], summary: LedgerSummary) -> str:
    """The ledger as a terminal table plus what it supports."""

    if not rows:
        return "No strategies in research.strategies."

    header = (
        f"{'STRATEGY':<34} {'VERDICT':<13} {'IR':>7} {'SHARPE':>7} {'FOLDS':>6} {'TRY':>4}  DIED OF"
    )
    lines = [header, "-" * len(header)]
    for row in rows:
        name = row.name[:32] if row.name else row.strategy_id[:32]
        lines.append(
            f"{name:<34} {row.verdict:<13} "
            f"{row.metric(row.information_ratio):>7} {row.metric(row.sharpe):>7} "
            f"{row.folds:>6} {row.attempts:>4}  {row.reason}"
        )

    lines.append("")
    lines.append("CORPUS")
    lines.extend(f"  {line}" for line in summary.lines())

    notes = observations(summary)
    if notes:
        lines.append("")
        lines.append("WHAT THIS SUPPORTS")
        lines.extend(f"  - {note}" for note in notes)

    lines.append("")
    lines.append(
        "Structural deaths measured the setup, not the idea. Read `learned about edge` "
        "as the honest count of experiments the firm has actually run."
    )
    return "\n".join(lines)


__all__ = [
    "EDGE_REASONS",
    "STRUCTURAL_REASONS",
    "LedgerReader",
    "LedgerRow",
    "LedgerSummary",
    "observations",
    "render",
    "row_from_mapping",
    "summarise",
]
