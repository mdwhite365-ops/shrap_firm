"""Does the promote metric predict anything, or is the firm ranking noise?

Mike, 2026-07-30, after thirteen evaluations and zero promotions: the shortest
path to a promotion is not more hypotheses, it is knowing whether the gate is
calibrated. This produces the evidence. **It does not move the gate** — the same
boundary ``guidance.py`` states out loud: the firm may measure whether a gate is
working; only Mike may move one.

Nothing here is imported by ``verdict.py``. The floors are inputs to this module
and outputs of nothing in it.

**The question that matters most cannot be answered yet, and saying so is the
point.** "Does an early fold's information ratio predict a late one's?" needs
the per-fold *sequence*. The evaluator computed that sequence on every run and
persisted only its mean and standard deviation, so for the twelve strategies
already killed it is gone — kills are terminal and ``evaluate`` refuses any
non-hypothesis strategy, so those runs cannot be reproduced. The sequence is
persisted from 2026-07-30 onward; until enough strategies carry it, this reports
the question as unanswerable rather than answering it from four points.

**What the existing corpus CAN answer**, and it is not nothing:

- what each candidate floor would have promoted, and what it would have let
  through — the counterfactual, stated as names rather than as a rate
- whether aggregate information ratio and fold consistency agree, because a
  gate on one that contradicts the other is picking a favourite
- how far the best strategy on record actually sits from the floor

**Every number here comes with its n.** Thirteen strategies support very few
conclusions. A correlation across thirteen points has error bars wide enough to
contain almost anything, and a module that printed one to three decimals without
saying so would be manufacturing confidence rather than measuring it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Below this many strategies carrying fold sequences, the early-vs-late question
# is not answerable and the report says so. Not a significance threshold — a
# correlation over fewer than this many points is a picture of two clusters, and
# reporting it as a number invites it to be read as a finding.
MIN_STRATEGIES_FOR_PERSISTENCE = 8

# Floors to report the counterfactual against. The live floor is passed in; these
# bracket it so the question "what would a lower bar have bought" is answerable
# without anyone editing code to ask it.
CANDIDATE_FLOORS: tuple[float, ...] = (0.25, 0.30, 0.35, 0.40, 0.50, 0.65)


@dataclass(frozen=True, slots=True)
class StrategyRun:
    """One evaluated strategy, reduced to what a calibration question needs."""

    strategy_id: str
    name: str
    verdict: str
    information_ratio: float | None
    sharpe: float | None
    consistency: float | None
    folds_with_active_edge: int | None
    n_folds: int | None
    fold_information_ratios: tuple[float, ...] = ()

    @property
    def has_sequence(self) -> bool:
        """Whether this run kept its per-fold ordering. False for everything
        evaluated before 2026-07-30."""

        return len(self.fold_information_ratios) >= 4

    @property
    def early_late(self) -> tuple[float, float] | None:
        """Mean IR of the first half of folds and of the second.

        Split at the midpoint rather than by date: folds are contiguous and
        equal-width by construction, so the halves are comparable spans.
        """

        if not self.has_sequence:
            return None
        irs = self.fold_information_ratios
        half = len(irs) // 2
        early, late = irs[:half], irs[half:]
        return sum(early) / len(early), sum(late) / len(late)


def run_from_mapping(row: Mapping[str, Any]) -> StrategyRun:
    consistency = row.get("consistency_metrics") or {}
    if not isinstance(consistency, Mapping):
        consistency = {}
    raw_sequence = consistency.get("fold_information_ratios")
    sequence = tuple(float(x) for x in raw_sequence) if isinstance(raw_sequence, list) else ()
    return StrategyRun(
        strategy_id=str(row["strategy_id"]),
        name=str(row.get("name") or ""),
        verdict=str(row.get("verdict") or "unevaluated"),
        information_ratio=_opt_float(row.get("information_ratio")),
        sharpe=_opt_float(row.get("sharpe")),
        consistency=_opt_float(consistency.get("consistency")),
        folds_with_active_edge=_opt_int(consistency.get("folds_with_active_edge")),
        n_folds=_opt_int(consistency.get("n_folds")),
        fold_information_ratios=sequence,
    )


def _opt_float(value: Any) -> float | None:
    if not isinstance(value, int | float | str):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _opt_int(value: Any) -> int | None:
    if not isinstance(value, int | float | str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Correlation, or ``None`` when it is not defined.

    No numpy: this runs wherever the ledger runs, and two loops are not worth a
    dependency edge into an image that may not carry one.
    """

    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    dx = [x - mean_x for x in xs]
    dy = [y - mean_y for y in ys]
    denominator = (sum(x * x for x in dx) * sum(y * y for y in dy)) ** 0.5
    if denominator <= 0.0:
        return None
    return float(sum(a * b for a, b in zip(dx, dy, strict=True)) / denominator)


@dataclass(frozen=True, slots=True)
class FloorCounterfactual:
    """What one candidate floor would have done to the corpus on record."""

    floor: float
    promoted: tuple[str, ...]
    """Names that would have cleared it. Names, not a count: a calibration is a
    decision about specific strategies, and a rate hides which ones."""

    @property
    def n(self) -> int:
        return len(self.promoted)

    def render(self) -> str:
        if not self.promoted:
            return f"  IR >= {self.floor:.2f}   nothing"
        names = ", ".join(n[:38] for n in self.promoted)
        return f"  IR >= {self.floor:.2f}   {self.n}: {names}"


@dataclass(frozen=True, slots=True)
class PersistenceReport:
    """Evidence for a calibration ruling. Decides nothing."""

    runs: tuple[StrategyRun, ...]
    live_floor: float
    counterfactuals: tuple[FloorCounterfactual, ...]
    ir_vs_consistency: float | None
    early_vs_late: float | None
    with_sequence: int

    @property
    def measured(self) -> tuple[StrategyRun, ...]:
        return tuple(r for r in self.runs if r.information_ratio is not None)

    @property
    def best(self) -> StrategyRun | None:
        ranked = [r for r in self.measured if r.information_ratio is not None]
        if not ranked:
            return None
        return max(ranked, key=lambda r: r.information_ratio or 0.0)

    def render(self) -> str:
        n = len(self.measured)
        if n == 0:
            return "No evaluated strategies — nothing to calibrate against."
        lines = [
            f"METRIC PERSISTENCE — {n} evaluated strategies, live IR floor {self.live_floor:.2f}",
            "",
        ]

        best = self.best
        if best is not None and best.information_ratio is not None:
            gap = self.live_floor - best.information_ratio
            lines.append(
                f"Best on record: {best.name[:52]} at IR {best.information_ratio:+.3f}"
                + (f", {gap:.3f} below the floor." if gap > 0 else ", above the floor.")
            )
            lines.append("")

        lines.append("WHAT EACH FLOOR WOULD HAVE PROMOTED")
        lines.extend(c.render() for c in self.counterfactuals)
        lines.append("")

        lines.append("DO THE TWO MEASURES AGREE?")
        if self.ir_vs_consistency is None:
            lines.append("  not enough strategies carry both to say.")
        else:
            lines.append(
                f"  aggregate IR vs fold consistency: r = {self.ir_vs_consistency:+.2f} "
                f"(n={n}). A gate on one that disagreed with the other would be "
                "picking a favourite."
            )
        lines.append("")

        lines.append("DOES AN EARLY FOLD PREDICT A LATE ONE?")
        if self.early_vs_late is None:
            lines.append(
                f"  UNANSWERABLE. {self.with_sequence} of {n} strategies carry their "
                f"per-fold sequence; {MIN_STRATEGIES_FOR_PERSISTENCE} are needed."
            )
            lines.append(
                "  The evaluator computed this sequence on every run and kept only "
                "its mean and standard deviation until 2026-07-30. For strategies "
                "already killed it cannot be recovered — kills are terminal and "
                "`evaluate` refuses any non-hypothesis strategy."
            )
            lines.append(
                "  This is the question the floor most depends on. If early folds do "
                "not predict late ones, the metric has no persistence and no floor "
                "is the right floor."
            )
        else:
            lines.append(
                f"  early-half vs late-half fold IR: r = {self.early_vs_late:+.2f} "
                f"(n={self.with_sequence})."
            )
        lines.append("")
        lines.append(
            f"n={n}. A correlation over this many points has error bars wide enough "
            "to contain almost anything. This is evidence for a ruling, not a "
            "ruling — nothing here is read by the verdict, and no floor moves "
            "without Mike."
        )
        return "\n".join(lines)


def analyse(rows: Sequence[Mapping[str, Any]], *, live_floor: float) -> PersistenceReport:
    """Turn ledger rows into calibration evidence. Pure; no I/O, no decisions."""

    runs = tuple(run_from_mapping(r) for r in rows)
    measured = [r for r in runs if r.information_ratio is not None]

    counterfactuals = tuple(
        FloorCounterfactual(
            floor=floor,
            promoted=tuple(
                r.name or r.strategy_id
                for r in sorted(measured, key=lambda r: -(r.information_ratio or 0.0))
                if (r.information_ratio or 0.0) >= floor
            ),
        )
        for floor in sorted({*CANDIDATE_FLOORS, live_floor})
    )

    paired = [
        (float(r.information_ratio), float(r.consistency))
        for r in measured
        if r.consistency is not None and r.information_ratio is not None
    ]
    ir_vs_consistency = pearson([a for a, _ in paired], [b for _, b in paired])

    sequenced = [r for r in runs if r.has_sequence]
    early_vs_late: float | None = None
    if len(sequenced) >= MIN_STRATEGIES_FOR_PERSISTENCE:
        halves = [r.early_late for r in sequenced]
        early_vs_late = pearson(
            [h[0] for h in halves if h is not None],
            [h[1] for h in halves if h is not None],
        )

    return PersistenceReport(
        runs=runs,
        live_floor=live_floor,
        counterfactuals=counterfactuals,
        ir_vs_consistency=ir_vs_consistency,
        early_vs_late=early_vs_late,
        with_sequence=len(sequenced),
    )


__all__ = [
    "CANDIDATE_FLOORS",
    "MIN_STRATEGIES_FOR_PERSISTENCE",
    "FloorCounterfactual",
    "PersistenceReport",
    "StrategyRun",
    "analyse",
    "pearson",
    "run_from_mapping",
]
