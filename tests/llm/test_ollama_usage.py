"""Ollama Cloud's usage meter, and the budget check built on it.

Context: every cost estimate this project has made against Ollama Cloud has been
wrong. First inferred from request counts and off by 2.7x; then corrected by
measuring the *weekly* window, which is not the window that stops runs. On
2026-09-20 a 599-item experiment was refused after 192 items with weekly usage at
27.7% and session usage at 100%.
"""

from __future__ import annotations

from typing import Any

from shrap.llm.ollama_usage import (
    PRODUCTION_RESERVE,
    UsageSnapshot,
    WindowUsage,
    check_budget,
    fetch_usage,
    parse_usage,
)

# The exact payload ollama.com returned on 2026-09-20, trimmed to the fields
# read here. Pinned as a fixture because the endpoint is undocumented: if it
# changes shape, this is the test that says so rather than a run that silently
# proceeds unchecked.
LIVE_PAYLOAD: dict[str, Any] = {
    "activity": {"cost": "0.00000", "models": []},
    "limits": {
        "session": {"usage": 1, "models": [{"name": "kimi-k3", "request_count": 974}]},
        "weekly": {
            "usage": 0.277,
            "models": [
                {"name": "kimi-k3", "request_count": 1268},
                {"name": "qwen3.5:397b", "request_count": 1455},
                {"name": "gpt-oss:20b", "request_count": 495},
            ],
        },
    },
}


class _Response:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Http:
    def __init__(self, response: _Response | Exception) -> None:
        self._response = response
        self.calls: list[tuple[str, Any]] = []

    async def get(self, url: str, *, headers: Any = None, timeout: Any = None) -> _Response:
        self.calls.append((url, headers))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def test_the_live_payload_parses_into_both_windows() -> None:
    snapshot = parse_usage(LIVE_PAYLOAD)

    assert snapshot is not None
    assert snapshot.session is not None
    assert snapshot.weekly is not None
    assert snapshot.session.usage == 1.0
    assert snapshot.session.requests == 974
    assert snapshot.weekly.usage == 0.277
    assert snapshot.weekly.requests == 1268 + 1455 + 495


def test_the_binding_window_is_the_fullest_one_not_the_weekly_one() -> None:
    """The whole point. Reading `weekly` alone said 27.7% free while the account
    was refusing every request."""

    snapshot = parse_usage(LIVE_PAYLOAD)

    assert snapshot is not None
    assert snapshot.binding is not None
    assert snapshot.binding.name == "session"
    assert snapshot.binding.spent


def test_an_unrecognisable_payload_is_none_rather_than_an_empty_snapshot() -> None:
    """A zeroed snapshot would tell a caller it has a full allowance — the most
    dangerous thing an unreadable meter could say."""

    assert parse_usage({"limits": {}}) is None
    assert parse_usage({"nothing": True}) is None
    assert parse_usage("not json at all") is None


def test_a_window_without_a_usage_number_is_dropped() -> None:
    snapshot = parse_usage({"limits": {"session": {"models": []}, "weekly": {"usage": 0.5}}})

    assert snapshot is not None
    assert snapshot.session is None
    assert snapshot.weekly is not None


async def test_fetch_sends_the_bearer_token() -> None:
    http = _Http(_Response(200, LIVE_PAYLOAD))

    snapshot = await fetch_usage(http, "sk-test")

    assert snapshot is not None
    _url, headers = http.calls[0]
    assert headers == {"Authorization": "Bearer sk-test"}


async def test_no_key_means_no_account_and_therefore_no_call() -> None:
    """A tier routed at the local daemon has no cloud quota to report, and
    probing ollama.com for one would be a request made for nothing."""

    http = _Http(_Response(200, LIVE_PAYLOAD))

    assert await fetch_usage(http, None) is None
    assert http.calls == []


async def test_every_failure_mode_fails_open() -> None:
    """The endpoint is undocumented. A courtesy check that can block the firm's
    work when Ollama changes a URL is a worse fault than the one it guards."""

    assert await fetch_usage(_Http(_Response(404, {})), "sk") is None
    assert await fetch_usage(_Http(_Response(200, ValueError("bad json"))), "sk") is None
    assert await fetch_usage(_Http(RuntimeError("connection refused")), "sk") is None


def test_a_spent_session_window_refuses_the_run() -> None:
    verdict = check_budget(parse_usage(LIVE_PAYLOAD))

    assert not verdict.ok
    assert "session" in verdict.reason


def test_the_reserve_refuses_before_the_window_is_actually_full() -> None:
    """Headroom for the always-on agents. The quota is account-wide: on
    2026-09-20 this experiment spent the session allowance and the Tech Watcher's
    hourly literature pass aborted with `scored: 0`."""

    nearly = UsageSnapshot(
        session=WindowUsage("session", 0.95, 900),
        weekly=WindowUsage("weekly", 0.20, 1200),
    )

    assert not check_budget(nearly, reserve=PRODUCTION_RESERVE).ok
    assert check_budget(nearly, reserve=0.0).ok


def test_an_unreadable_meter_proceeds_and_says_it_could_not_check() -> None:
    verdict = check_budget(None)

    assert verdict.ok
    assert "could not be read" in verdict.reason


def test_a_healthy_account_reports_both_windows_in_the_reason() -> None:
    healthy = UsageSnapshot(
        session=WindowUsage("session", 0.10, 40),
        weekly=WindowUsage("weekly", 0.20, 1200),
    )

    verdict = check_budget(healthy)

    assert verdict.ok
    assert "session 10.0% used" in verdict.reason
    assert "weekly 20.0% used" in verdict.reason
