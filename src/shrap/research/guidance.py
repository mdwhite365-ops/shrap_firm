"""What the corpus says to try next — and what it is not allowed to say.

The ledger reads results. Nothing acts on them. A proposer that cannot see that
four of six factor strategies lost to buy-and-hold will keep proposing factor
strategies, and the firm will run the same experiment until it gets a lucky
draw. That is the failure the multiple-testing gate punishes after the fact;
this module is the half that avoids it beforehand.

**The boundary, which is the whole design.** This informs *what to propose*. It
must never inform *what counts as success*. A system that can adjust its own
gate will adjust it until something passes — the same gradient that produces
p-hacking, arrived at honestly. So:

- no output of this module is read by ``verdict.py``
- no output names a floor, a threshold, or a promote criterion
- the only thing it can change is which hypothesis is tried next

Mike ratifies gate changes on evidence (the decision-by-merge convention). The
firm may *measure* whether a gate is working; it may not *move* one.

**It reports its own evidence strength.** Twelve evaluations support very few
conclusions, and a guidance layer that stated them with confidence would be
worse than none — it would narrow the search on noise. Every observation carries
the count behind it, and thin evidence is labelled thin.

**The dimension list is authored, not discovered.** :data:`DIMENSIONS` is a
hand-written account of the axes a strategy can vary along. That is a real
limitation: the firm cannot notice an untried dimension nobody thought to name.
Recorded here rather than left implicit — it is the ceiling on how surprising
this module's suggestions can be.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Below this many *edge* findings, the corpus cannot support a claim that a
# dimension is exhausted. Four is not a statistical threshold — it is the point
# at which "everything we tried here failed" stops being a coin-flip story.
MIN_FINDINGS_FOR_EXHAUSTED = 4

# The axes a strategy can vary along. Authored — see the module docstring.
# Each entry maps a dimension to how it is read off a strategy's spec.
DIMENSIONS: tuple[str, ...] = (
    "rule-family",
    "signal-input",
    "horizon",
    "universe",
    "bar-frequency",
)

# Signal inputs, derived from which panel series a rule actually consults. The
# firm has never had a strategy that reads anything but closes and volumes,
# because those are the only series `PanelWindow` exposes.
SIGNAL_PRICE = "price"
SIGNAL_VOLUME = "volume"

# Rules whose signal is volume rather than price. Small and explicit rather than
# inferred: guessing from parameter names would silently misclassify.
VOLUME_FACTORS: frozenset[str] = frozenset({"volume-shock"})


@dataclass(frozen=True, slots=True)
class StrategyShape:
    """What one strategy varied, independent of how it performed."""

    strategy_id: str
    name: str
    rule: str
    factor: str | None
    signal: str
    horizon: int | None
    n_tickers: int
    tested: bool
    """The engine ran and produced a finding about edge."""

    beat_benchmark: bool


def shape_of(raw: Mapping[str, Any]) -> StrategyShape:
    """Read a strategy's shape from its spec, never from its name."""

    spec = raw.get("spec") or {}
    params = spec.get("params") or {} if isinstance(spec, Mapping) else {}
    rule = str(spec.get("rule", "reference-trend")) if isinstance(spec, Mapping) else "unknown"
    factor = params.get("factor")
    factor_name = str(factor) if factor is not None else None
    horizon = params.get("lookback") or params.get("slow")
    tickers = raw.get("tickers") or {}
    longs = tickers.get("long", []) if isinstance(tickers, Mapping) else []
    ir = raw.get("information_ratio")
    return StrategyShape(
        strategy_id=str(raw["strategy_id"]),
        name=str(raw.get("name") or ""),
        rule=rule,
        factor=factor_name,
        signal=SIGNAL_VOLUME if factor_name in VOLUME_FACTORS else SIGNAL_PRICE,
        horizon=int(horizon) if horizon is not None else None,
        n_tickers=len(longs) if isinstance(longs, list) else 0,
        tested=bool(raw.get("tested")),
        beat_benchmark=bool(ir is not None and float(ir) > 0.0),
    )


@dataclass(frozen=True, slots=True)
class Observation:
    """One thing the corpus supports, with the evidence behind it."""

    statement: str
    findings: int
    """How many *tested* strategies back this. The reader's confidence dial."""

    @property
    def is_thin(self) -> bool:
        return self.findings < MIN_FINDINGS_FOR_EXHAUSTED

    def render(self) -> str:
        mark = "~" if self.is_thin else "-"
        strength = "thin" if self.is_thin else f"n={self.findings}"
        return f"{mark} {self.statement}  [{strength}]"


@dataclass(frozen=True, slots=True)
class Guidance:
    """What to try next, and how much to trust it."""

    tested: int
    exhausted: tuple[Observation, ...] = ()
    """Dimensions where everything tried has failed."""

    untried: tuple[Observation, ...] = ()
    """Dimensions the corpus has never varied at all."""

    warnings: tuple[Observation, ...] = ()
    """Correlated-failure risks — reasons several results may be one result."""

    def render(self) -> str:
        if self.tested == 0:
            return "No tested strategies yet — the corpus supports no guidance."
        lines = [f"Derived from {self.tested} tested strategies.", ""]
        for title, group in (
            ("EXHAUSTED — tried and failed", self.exhausted),
            ("UNTRIED — never varied", self.untried),
            ("WARNINGS — why these results may be one result", self.warnings),
        ):
            if not group:
                continue
            lines.append(title)
            lines.extend(f"  {o.render()}" for o in group)
            lines.append("")
        lines.append(
            "`~` marks an observation with too little evidence to act on alone. "
            "This informs WHAT TO PROPOSE and never what counts as success — no "
            "output here is read by the verdict."
        )
        return "\n".join(lines)


def _exhausted(shapes: Sequence[StrategyShape]) -> list[Observation]:
    tested = [s for s in shapes if s.tested]
    out: list[Observation] = []

    by_rule: dict[str, list[StrategyShape]] = {}
    for s in tested:
        by_rule.setdefault(s.rule, []).append(s)
    for rule, members in sorted(by_rule.items()):
        winners = [m for m in members if m.beat_benchmark]
        if members and not winners:
            out.append(
                Observation(
                    f"every `{rule}` strategy lost to equal-weight buy-and-hold "
                    f"({len(members)} tried)",
                    findings=len(members),
                )
            )

    if tested and not any(s.beat_benchmark for s in tested):
        out.append(
            Observation(
                "no strategy of any kind has beaten the benchmark — suspect the "
                "universe or the benchmark before the rules",
                findings=len(tested),
            )
        )
    return out


def _untried(shapes: Sequence[StrategyShape]) -> list[Observation]:
    tested = [s for s in shapes if s.tested]
    out: list[Observation] = []
    if not tested:
        return out

    signals = {s.signal for s in tested}
    if signals == {SIGNAL_PRICE}:
        out.append(
            Observation(
                "every strategy reads price alone — a correlated failure across "
                "them is indistinguishable from a defect in how prices are read",
                findings=len(tested),
            )
        )

    universes = {s.n_tickers for s in tested if s.n_tickers}
    if len(universes) <= 1:
        out.append(
            Observation(
                "one universe has been used throughout — a result about these "
                "names is not yet a result about the effect",
                findings=len(tested),
            )
        )

    horizons = {s.horizon for s in tested if s.horizon}
    if horizons and min(horizons) >= 5:
        out.append(
            Observation(
                "nothing shorter than a week has been tried; the fast layer "
                "needs intraday bars the firm does not store",
                findings=len(tested),
            )
        )
    return out


def _warnings(shapes: Sequence[StrategyShape]) -> list[Observation]:
    tested = [s for s in shapes if s.tested]
    out: list[Observation] = []
    if len(tested) < 2:
        return out

    if len({s.n_tickers for s in tested if s.n_tickers}) <= 1:
        out.append(
            Observation(
                "all strategies share one universe over one window, so their "
                "failures are not independent evidence",
                findings=len(tested),
            )
        )

    winners = [s for s in tested if s.beat_benchmark]
    if 0 < len(winners) < len(tested):
        names = ", ".join(sorted(w.name[:28] for w in winners))
        out.append(
            Observation(
                f"the survivors ({names}) have not been checked for whether they "
                "win in the SAME folds — if they do, the firm holds one effect",
                findings=len(winners),
            )
        )
    return out


def derive(rows: Sequence[Mapping[str, Any]]) -> Guidance:
    """Turn the corpus into guidance. Pure; no I/O."""

    shapes = [shape_of(r) for r in rows]
    tested = sum(1 for s in shapes if s.tested)
    return Guidance(
        tested=tested,
        exhausted=tuple(_exhausted(shapes)),
        untried=tuple(_untried(shapes)),
        warnings=tuple(_warnings(shapes)),
    )


__all__ = [
    "DIMENSIONS",
    "MIN_FINDINGS_FOR_EXHAUSTED",
    "SIGNAL_PRICE",
    "SIGNAL_VOLUME",
    "VOLUME_FACTORS",
    "Guidance",
    "Observation",
    "StrategyShape",
    "derive",
    "shape_of",
]
