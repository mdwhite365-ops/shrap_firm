"""Account rotation: which strategy each paper account should hold, from the shadow record.

**The accounts are three slots (ADR-0017), and the shadow ledger (#279) is the
only place every candidate is measured on the same out-of-sample days.** This
module reads that record and says, per slot, whether a challenger has earned the
account. It **recommends; it never moves anything.** A promotion into a trading
stage is a human act under ADR-0015, and the transition log's ``actor`` column
exists so that a later reader can tell the firm's verdicts from Mike's. The
output is the exact ``shrap-strategy-stage`` commands, for Mike to run or not.

**The comparison is paired, not two leaderboard positions.** Two strategies'
IRs each carry a standard error of ~2 annualised after 60 sessions, so reading
them side by side says nothing. The difference of their daily active returns,
on the sessions both were settled, is one series with its own IR and its own
standard error (the same Lo 2002 form the ledger uses), and because both
strategies hold overlapping names in the same market, it is far less noisy than
either alone. A challenger replaces an incumbent only when that paired IR
clears ``z`` standard errors.

**An empty slot is filled against zero, not against an incumbent.** The
challenger's own shadow IR must clear ``z`` standard errors above nothing. Mike's
2026-09-23 ruling for the third account is exactly this: empty until something
is worth trying.

**What rotation does not decide.** Positions: the Runner trades only the tickers
in its strategy's universe, so anything the outgoing strategy held outside the
incoming one's universe is orphaned and must be closed by hand. The report
names them when it can.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from shrap.research.shadow_ledger import TRADING_DAYS_PER_YEAR
from shrap.research.strategy_registry import STATUS_KILLED

DEFAULT_MIN_SESSIONS = 60
"""Settled shadow sessions a strategy needs before it is ranked at all.

About three months. Below this the SE of an annualised IR is above ~2, and a
candidate that clears ``z`` on so little is more likely a lucky month than an
edge."""

DEFAULT_Z = 1.0
"""Standard errors a challenger's paired IR must clear.

One, not two. A paper slot is cheap to be wrong about and cheap to reverse, and
at two the firm would wait roughly four times as long for every swap; the
backtest cannot supply the difference (KI-036). **This is a calibration and it
is Mike's**: merging the card that introduced it is accepting it."""

SlotSeries = Sequence[tuple[date, float, float]]
"""A strategy's settled shadow rows: ``(session_date, net_return, benchmark_return)``."""


@dataclass(frozen=True, slots=True)
class Slot:
    account_id: str
    incumbent_id: str | None


@dataclass(frozen=True, slots=True)
class PairedEdge:
    """The challenger minus the reference, on the sessions both were settled."""

    sessions: int
    information_ratio: float | None
    standard_error: float | None

    @property
    def z(self) -> float | None:
        if self.information_ratio is None or not self.standard_error:
            return None
        return self.information_ratio / self.standard_error


@dataclass(frozen=True, slots=True)
class Recommendation:
    account_id: str
    incumbent_id: str | None
    challenger_id: str | None
    edge: PairedEdge | None
    reason: str

    @property
    def swap(self) -> bool:
        return self.challenger_id is not None


def _active(rows: SlotSeries) -> dict[date, float]:
    return {d: net - bench for d, net, bench in rows}


def paired_edge(challenger: SlotSeries, reference: SlotSeries | None) -> PairedEdge:
    """Annualised IR and SE of ``challenger`` active return minus ``reference``'s.

    ``reference=None`` compares against zero: the challenger's own active return.
    Only sessions both sides settled are used, so a gap in either is a gap in
    the pair, never an imputed zero.
    """

    a = _active(challenger)
    if reference is None:
        diffs = [a[d] for d in sorted(a)]
    else:
        b = _active(reference)
        diffs = [a[d] - b[d] for d in sorted(a.keys() & b.keys())]
    n = len(diffs)
    if n < 2:
        return PairedEdge(n, None, None)
    mean = sum(diffs) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in diffs) / (n - 1))
    if sd == 0.0:
        return PairedEdge(n, None, None)
    ir_daily = mean / sd
    root = math.sqrt(TRADING_DAYS_PER_YEAR)
    return PairedEdge(
        sessions=n,
        information_ratio=ir_daily * root,
        standard_error=math.sqrt((1.0 + ir_daily**2 / 2.0) / n) * root,
    )


def recommend(
    slots: Sequence[Slot],
    series: Mapping[str, SlotSeries],
    candidates: Sequence[str],
    *,
    min_sessions: int = DEFAULT_MIN_SESSIONS,
    z: float = DEFAULT_Z,
) -> list[Recommendation]:
    """One recommendation per slot. A challenger is used for at most one slot.

    ``candidates`` are the strategies eligible to take a slot (not already
    holding one, dailies only — the caller decides). Slots are considered in
    the order given; each takes the strongest remaining challenger that clears
    ``z`` against it.
    """

    taken: set[str] = {s.incumbent_id for s in slots if s.incumbent_id}
    out: list[Recommendation] = []
    for slot in slots:
        incumbent = series.get(slot.incumbent_id or "", ()) if slot.incumbent_id else None
        best: tuple[float, str, PairedEdge] | None = None
        for cid in candidates:
            if cid in taken:
                continue
            rows = series.get(cid, ())
            if len(rows) < min_sessions:
                continue
            edge = paired_edge(rows, incumbent)
            if edge.sessions < min_sessions or edge.z is None or edge.z < z:
                continue
            if best is None or edge.z > best[0]:
                best = (edge.z, cid, edge)
        if best is None:
            what = "the incumbent" if slot.incumbent_id else "zero"
            out.append(
                Recommendation(
                    slot.account_id,
                    slot.incumbent_id,
                    None,
                    None,
                    f"no candidate with {min_sessions}+ shared sessions clears {z:g} SE "
                    f"against {what}",
                )
            )
            continue
        _, cid, edge = best
        taken.add(cid)
        out.append(
            Recommendation(
                slot.account_id,
                slot.incumbent_id,
                cid,
                edge,
                f"paired IR {edge.information_ratio:+.2f} +/- {edge.standard_error:.2f} "
                f"over {edge.sessions} sessions ({edge.z:.1f} SE)",
            )
        )
    return out


def commands(rec: Recommendation, names: Mapping[str, str]) -> list[str]:
    """The ``shrap-strategy-stage`` commands that would carry out ``rec``, in order.

    The outgoing strategy goes to ``kill-review``: it stops trading, stays in the
    shadow ledger, and can return to ``paper`` later — unlike ``retired``, which
    is terminal and leaves the ledger. Its account is cleared before the new
    assignment because one account holds one strategy (a unique index).
    """

    if not rec.swap or rec.challenger_id is None:
        return []
    run = "docker compose --profile tools run --rm strategy-evaluator shrap-strategy-stage"
    why = rec.reason.replace('"', "'")
    lines: list[str] = []
    if rec.incumbent_id:
        lines += [
            f'{run} move {rec.incumbent_id} --to kill-review --reason "rotated out of '
            f"{rec.account_id} by the shadow record: {names.get(rec.challenger_id, '?')} "
            f'beat it, {why}"',
            f"{run} assign-account {rec.incumbent_id} --clear",
        ]
    lines += [
        f"{run} assign-account {rec.challenger_id} --account-id {rec.account_id}",
        f'{run} move {rec.challenger_id} --to paper --reason "rotated into {rec.account_id} '
        f'by the shadow record: {why}"',
    ]
    return lines


def render(
    recs: Sequence[Recommendation],
    names: Mapping[str, str],
    *,
    orphaned: Mapping[str, Sequence[str]] | None = None,
    statuses: Mapping[str, str] | None = None,
) -> str:
    lines = [
        "Account rotation — recommendations from the shadow forward test. Nothing is moved.",
        "A swap needs the challenger's active return, minus the incumbent's on the same",
        "sessions, to clear the threshold in standard errors. An empty slot compares to zero.",
        "",
    ]
    for rec in recs:
        who = names.get(rec.incumbent_id or "", "(empty)") if rec.incumbent_id else "(empty)"
        lines.append(f"{rec.account_id}: holds {who}")
        if not rec.swap:
            lines.append(f"  keep — {rec.reason}")
            lines.append("")
            continue
        lines.append(f"  SWAP IN {names.get(rec.challenger_id or '', '?')} — {rec.reason}")
        if (statuses or {}).get(rec.challenger_id or "") == STATUS_KILLED:
            # ``killed`` is terminal, and a revision would be re-evaluated and
            # re-killed on the same evidence within one sweep. Whether the shadow
            # record may overrule a backtest kill is the min_trades protocol
            # question, which is Mike's; the report states the finding and stops.
            lines.append(
                "  The challenger was KILLED on backtest evidence and the stage machine "
                "cannot revive it. The shadow record now disagrees with that kill: "
                "reviving it is a protocol ruling, not a command."
            )
            lines.append("")
            continue
        for cmd in commands(rec, names):
            lines.append(f"    {cmd}")
        left = (orphaned or {}).get(rec.account_id, ())
        if left:
            lines.append(
                "  Positions outside the incoming universe, which the Runner will not "
                f"trade and must be closed by hand: {', '.join(left)}"
            )
        lines.append("")
    return "\n".join(lines).rstrip()


__all__ = [
    "DEFAULT_MIN_SESSIONS",
    "DEFAULT_Z",
    "PairedEdge",
    "Recommendation",
    "Slot",
    "commands",
    "paired_edge",
    "recommend",
    "render",
]
