"""Read the persisted Kelly posterior for a strategy.

The Strategy Evaluator writes ``SkillPosterior.to_payload()`` into
``research.evaluations.active_metrics->'posterior'``. This module reads it back
for the Risk Officer's sizing path.

**Why a missing posterior must not fall back to the flat fraction.** Every
strategy this firm has measured has a posterior fraction below the flat 0.25
stage fraction — momentum 126/21 sizes at 0.147. If a database error or a
missing row silently fell back to 0.25, the firm would size positions UP at
exactly the moment it lost the ability to justify them. That is fail-open.
The caller is expected to refuse the intent rather than substitute a default;
this module only distinguishes "no posterior exists" (``None``) from "the
database cannot be read" (:class:`PosteriorUnavailable`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol

from shrap.risk_compliance.risk_officer.posterior import SkillPosterior

# The alias is load-bearing. Postgres names an un-aliased operator expression
# `?column?`, so `row["posterior"]` would return nothing for every strategy
# forever — and a unit test against a fake pool keyed on "posterior" passes
# regardless, because the fake chooses its own column names. Verified against
# the real database rather than reasoned about: exactly the #212-#214 shape,
# where three PRs were needed for one backup because each failure was only
# visible on the next real run.
#
# `active_metrics ? 'posterior'` skips evaluation rows written before the
# posterior existed. Without it those come back as a SQL NULL, which is
# indistinguishable from "the newest evaluation has no posterior" and would
# quietly pin a strategy to the oldest row that happens to have one.
# The Evaluator's protocol version, duplicated rather than imported.
#
# `research.strategy_evaluator.engine.PROTOCOL_VERSION` is the authority, but
# importing it pulls numpy into the Pre-Trade Checker's import path for one
# string — the same reason `posterior.PRIOR_IR` is a local copy of the promote
# floor. A test asserts the two are equal, so they cannot drift silently.
EVAL_PROTOCOL_VERSION = "0.2"

SELECT_LATEST_POSTERIOR_SQL = """
SELECT active_metrics->'posterior' AS posterior
FROM research.evaluations
WHERE strategy_id = $1
  AND protocol_version = $2
  AND (active_metrics->>'n_periods')::int > 0
  AND active_metrics ? 'posterior'
ORDER BY created_at DESC
LIMIT 1
""".strip()


class AsyncConnection(Protocol):
    async def fetchrow(self, query: str, *args: object) -> Mapping[str, object] | None: ...


class AcquireContext(Protocol):
    async def __aenter__(self) -> AsyncConnection: ...
    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class AsyncPool(Protocol):
    def acquire(self) -> AcquireContext: ...


class PosteriorUnavailable(Exception):
    """The database could not be read to obtain a posterior.

    Distinct from "no posterior exists", which is a legitimate ``None``.
    """


def parse_posterior(payload: str | Mapping[str, object] | None) -> SkillPosterior | None:
    """Reconstruct a ``SkillPosterior`` from its persisted JSON payload.

    ``None`` or an empty payload means no posterior exists. Malformed JSON or a
    payload missing ``mean_ir``/``sd_ir`` also returns ``None``: a garbled row
    must not be able to size a position. ``fraction`` and ``evidence_based`` are
    derived properties and are not passed to the constructor.
    """

    if payload is None:
        return None

    if isinstance(payload, str):
        if not payload.strip():
            return None
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None
    else:
        data = payload

    if not isinstance(data, Mapping):
        return None

    try:
        mean_ir = float(data["mean_ir"])
        sd_ir = float(data["sd_ir"])
    except (KeyError, TypeError, ValueError):
        return None

    n_sessions = data.get("n_sessions")
    # `isinstance(True, int)` is True, so a JSON `true` would otherwise become a
    # session count of 1 and make a prior-only belief look evidence-based.
    if not isinstance(n_sessions, int) or isinstance(n_sessions, bool):
        return None

    observed_ir = data.get("observed_ir")
    if observed_ir is not None and not isinstance(observed_ir, int | float):
        return None

    backtest_ir = data.get("backtest_ir")
    if backtest_ir is not None and not isinstance(backtest_ir, int | float):
        return None

    backtest_sd = data.get("backtest_sd")
    if backtest_sd is not None and not isinstance(backtest_sd, int | float):
        return None

    return SkillPosterior(
        mean_ir=mean_ir,
        sd_ir=sd_ir,
        n_sessions=n_sessions,
        observed_ir=float(observed_ir) if observed_ir is not None else None,
        backtest_ir=float(backtest_ir) if backtest_ir is not None else None,
        backtest_sd=float(backtest_sd) if backtest_sd is not None else None,
    )


class PostgresPosteriorReader:
    """Reads the latest posterior for a strategy from Postgres."""

    def __init__(self, pool: AsyncPool, *, protocol_version: str) -> None:
        self._pool = pool
        self._protocol_version = protocol_version

    async def latest_posterior(self, strategy_id: str) -> SkillPosterior | None:
        """Return the most recent posterior for ``strategy_id``, or ``None``.

        Raises :class:`PosteriorUnavailable` if the database cannot be read.
        """

        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    SELECT_LATEST_POSTERIOR_SQL, strategy_id, self._protocol_version
                )
        except Exception as exc:
            raise PosteriorUnavailable(
                f"could not read posterior for strategy {strategy_id}"
            ) from exc

        if row is None:
            return None

        payload = row.get("posterior")
        if payload is not None and not isinstance(payload, str | Mapping):
            # A driver returning some other shape is a wiring fault, not an
            # absent posterior, and must not read as "size this flat".
            raise PosteriorUnavailable(
                f"posterior for strategy {strategy_id} came back as {type(payload).__name__}"
            )
        return parse_posterior(payload)


__all__ = [
    "EVAL_PROTOCOL_VERSION",
    "SELECT_LATEST_POSTERIOR_SQL",
    "AcquireContext",
    "AsyncConnection",
    "AsyncPool",
    "PosteriorUnavailable",
    "PostgresPosteriorReader",
    "parse_posterior",
]
