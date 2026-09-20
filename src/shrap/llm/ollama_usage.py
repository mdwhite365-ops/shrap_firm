"""Read Ollama Cloud's own usage meter instead of estimating it.

Every cost estimate this project has made against Ollama Cloud has been wrong,
and wrong in a different way each time:

- Inferred from request counts, and off by 2.7x.
- Corrected by measuring one window — the **weekly** one — and then used to
  budget a run that the **session** window stopped two thirds of the way in.

``https://ollama.com/api/usage`` answers both directly, for the cost of one GET
with the key the firm already holds. On 2026-09-20 it reported
``limits.session.usage = 1.0`` while ``limits.weekly.usage`` was ``0.277``: the
number this project had been budgeting against was not the number that stops
runs.

**The quota is account-wide.** The production Tech Watcher filter, the Hypothesis
Generator and any experiment all draw on the same windows, so an experiment that
spends the session allowance takes the production filter down with it — which is
exactly what happened on 2026-09-20, when the literature pass aborted with
``scored: 0`` after five consecutive 429s. :data:`PRODUCTION_RESERVE` exists so a
batch job leaves the agents something to run on.

The endpoint is undocumented, so every read here **fails open**: an unreadable
meter returns ``None`` and the caller proceeds while saying it could not check.
A courtesy check that can block the firm's work when Ollama changes a URL would
be a worse fault than the one it guards against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import structlog

log = structlog.get_logger(__name__)

OLLAMA_USAGE_URL = "https://ollama.com/api/usage"

USAGE_TIMEOUT_SECONDS = 15.0

# Headroom a batch job leaves for the always-on agents. Not tuned — a first cut
# chosen because the hourly Tech Watcher literature pass is a few dozen calls and
# 10% of a window has always been far more than that. Raise it if an agent is
# ever starved with a batch job inside its reserve.
PRODUCTION_RESERVE = 0.10

WINDOW_SESSION = "session"
WINDOW_WEEKLY = "weekly"


@dataclass(frozen=True, slots=True)
class WindowUsage:
    """One rate-limit window as Ollama reports it.

    ``usage`` is a fraction of the allowance, ``1.0`` meaning spent. It is
    **cost-weighted**, not a request count — the two move at different rates per
    model, which is why ``requests`` is carried alongside rather than used as a
    proxy for it.
    """

    name: str
    usage: float
    requests: int

    @property
    def spent(self) -> bool:
        return self.usage >= 1.0

    def headroom(self, reserve: float = 0.0) -> float:
        """How much of this window is free, after holding ``reserve`` back."""

        return max(0.0, 1.0 - self.usage - reserve)

    def describe(self) -> str:
        return f"{self.name} {self.usage:.1%} used ({self.requests} requests)"


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """Both windows at one instant."""

    session: WindowUsage | None
    weekly: WindowUsage | None

    @property
    def windows(self) -> tuple[WindowUsage, ...]:
        return tuple(w for w in (self.session, self.weekly) if w is not None)

    @property
    def binding(self) -> WindowUsage | None:
        """The window closest to full — the one that will actually stop a run.

        Reading only ``weekly`` is the mistake this property exists to make hard
        to repeat.
        """

        windows = self.windows
        return max(windows, key=lambda w: w.usage) if windows else None

    def describe(self) -> str:
        if not self.windows:
            return "ollama usage: no windows reported"
        return "ollama usage: " + ", ".join(w.describe() for w in self.windows)


class _Response(Protocol):
    status_code: int

    def json(self) -> Any: ...


class _HttpClient(Protocol):
    async def get(self, url: str, *, headers: Any = ..., timeout: Any = ...) -> _Response: ...


def _parse_window(name: str, raw: Any) -> WindowUsage | None:
    if not isinstance(raw, dict):
        return None
    usage = raw.get("usage")
    if not isinstance(usage, int | float):
        return None
    models = raw.get("models")
    requests = 0
    if isinstance(models, list):
        for entry in models:
            if isinstance(entry, dict) and isinstance(entry.get("request_count"), int):
                requests += int(entry["request_count"])
    return WindowUsage(name=name, usage=float(usage), requests=requests)


def parse_usage(payload: Any) -> UsageSnapshot | None:
    """Turn the endpoint's JSON into a snapshot, or ``None`` if unrecognisable.

    Returning ``None`` rather than a zeroed snapshot is deliberate: a snapshot
    reading 0% would tell a caller it has a full allowance, which is the most
    dangerous thing an unreadable meter could say.
    """

    if not isinstance(payload, dict):
        return None
    limits = payload.get("limits")
    if not isinstance(limits, dict):
        return None
    session = _parse_window(WINDOW_SESSION, limits.get(WINDOW_SESSION))
    weekly = _parse_window(WINDOW_WEEKLY, limits.get(WINDOW_WEEKLY))
    if session is None and weekly is None:
        return None
    return UsageSnapshot(session=session, weekly=weekly)


async def fetch_usage(
    http: _HttpClient, api_key: str | None, *, url: str = OLLAMA_USAGE_URL
) -> UsageSnapshot | None:
    """Read both windows. ``None`` on any failure, never an exception.

    ``api_key`` of ``None`` means the caller is routed at the local daemon, which
    has no account and therefore no quota to report.
    """

    if not api_key:
        return None
    try:
        response = await http.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=USAGE_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            log.warning("ollama_usage.unavailable", status=response.status_code)
            return None
        snapshot = parse_usage(response.json())
    except Exception as exc:  # an unreadable meter must not stop the work
        log.warning("ollama_usage.failed", error=f"{type(exc).__name__}: {exc}"[:200])
        return None
    if snapshot is None:
        log.warning("ollama_usage.unrecognised_payload")
    return snapshot


@dataclass(frozen=True, slots=True)
class BudgetVerdict:
    """Whether a batch job of a known size should start now."""

    ok: bool
    reason: str
    snapshot: UsageSnapshot | None


def check_budget(
    snapshot: UsageSnapshot | None, *, reserve: float = PRODUCTION_RESERVE
) -> BudgetVerdict:
    """Refuse a run whose binding window is already inside the reserve.

    This deliberately does **not** predict whether a run of N completions will
    fit. ``usage`` is cost-weighted and the weighting is not published, so any
    completions-to-fraction conversion would be the same guesswork that produced
    the 2.7x error — a number that looks like a measurement and is not. The
    check answers the one question the data supports: *is there room to start.*
    """

    if snapshot is None:
        return BudgetVerdict(
            ok=True,
            reason="ollama usage could not be read — proceeding unchecked",
            snapshot=None,
        )
    binding = snapshot.binding
    if binding is None:
        return BudgetVerdict(ok=True, reason="no windows reported", snapshot=snapshot)
    if binding.headroom(reserve) <= 0.0:
        return BudgetVerdict(
            ok=False,
            reason=(
                f"{binding.describe()} — at or inside the {reserve:.0%} reserve held "
                "for the always-on agents, which share this account's quota"
            ),
            snapshot=snapshot,
        )
    return BudgetVerdict(ok=True, reason=snapshot.describe(), snapshot=snapshot)


__all__ = [
    "OLLAMA_USAGE_URL",
    "PRODUCTION_RESERVE",
    "WINDOW_SESSION",
    "WINDOW_WEEKLY",
    "BudgetVerdict",
    "UsageSnapshot",
    "WindowUsage",
    "check_budget",
    "fetch_usage",
    "parse_usage",
]
