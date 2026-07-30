"""Kill-switch state in Redis, history in Postgres.

The order path reads switch state on every intent, so it lives in a Redis hash
(``risk:switches``) rather than behind a query. Every transition is also
appended to ``risk.kill_switches``, which is the authority if the two disagree —
the hash is a cache of a log, and :meth:`RedisSwitchStore.rebuild` restores it.

**Reads fail closed.** If Redis cannot be reached, the board cannot be known,
and an unknown board must halt rather than approve. That is the opposite of the
usual cache behaviour and it is deliberate: the spec calls a Risk Officer that
fails open "among the firm's worst failure modes", while one that fails closed
"halts trading but is safe".
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol

import structlog

from shrap.risk_compliance.risk_officer.switches import SwitchBoard, SwitchState

log = structlog.get_logger(__name__)

SWITCH_HASH_KEY = "risk:switches"


class SwitchStateUnavailable(Exception):
    """Switch state could not be read. Treat the firm as halted."""


class SwitchRedis(Protocol):
    async def hgetall(self, name: str) -> dict[str, str]: ...

    async def hset(self, name: str, key: str, value: str) -> Any: ...

    async def delete(self, *names: str) -> Any: ...


def _encode(state: SwitchState) -> str:
    return json.dumps(
        {
            "active": state.active,
            "actor": state.actor,
            "reason": state.reason,
            "at": state.at.isoformat(),
        }
    )


def _decode(name: str, raw: str) -> SwitchState | None:
    try:
        data = json.loads(raw)
        return SwitchState(
            name=name,
            active=bool(data["active"]),
            actor=str(data["actor"]),
            reason=str(data["reason"]),
            at=datetime.fromisoformat(str(data["at"])),
        )
    except Exception:
        # A corrupt entry is not a licence to trade. Dropping it here would
        # silently clear a switch, so it is reported and the caller rebuilds
        # from Postgres.
        log.error("risk_officer.switch_entry_corrupt", switch=name, raw=raw, exc_info=True)
        return None


class RedisSwitchStore:
    """The live switch board."""

    def __init__(self, redis: SwitchRedis, key: str = SWITCH_HASH_KEY) -> None:
        self._redis = redis
        self._key = key

    async def load(self) -> SwitchBoard:
        """Read the current board, or raise :class:`SwitchStateUnavailable`."""

        try:
            raw = await self._redis.hgetall(self._key)
        except Exception as exc:
            raise SwitchStateUnavailable(
                "cannot read risk:switches from Redis, so the firm's halt state is "
                "unknown. Refusing to approve intents against an unknown board."
            ) from exc
        states = []
        corrupt = 0
        for name, value in (raw or {}).items():
            state = _decode(str(name), str(value))
            if state is None:
                corrupt += 1
                continue
            states.append(state)
        if corrupt:
            raise SwitchStateUnavailable(
                f"{corrupt} corrupt entries in risk:switches — rebuild from "
                "risk.kill_switches before trading."
            )
        return SwitchBoard(states)

    async def save(self, state: SwitchState) -> None:
        await self._redis.hset(self._key, state.name, _encode(state))

    async def rebuild(self, states: tuple[SwitchState, ...]) -> None:
        """Replace the hash from the Postgres log."""

        await self._redis.delete(self._key)
        for state in states:
            await self.save(state)
        log.info("risk_officer.switches_rebuilt", count=len(states))


__all__ = [
    "SWITCH_HASH_KEY",
    "RedisSwitchStore",
    "SwitchRedis",
    "SwitchStateUnavailable",
]
