"""Tests for the market data client and the Regime Classifier run pass."""

from __future__ import annotations

import tomllib
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from shrap.events import Envelope, EventPublisher, normalize_redis_fields
from shrap.intelligence.market_data import (
    AlpacaMarketDataClient,
    AlpacaMarketDataSettings,
    DailyBar,
)
from shrap.intelligence.regime.agent import (
    STREAM_REGIME_CHANGED,
    STREAM_REGIME_SIZING_MODIFIER,
    STREAM_REGIME_TICK,
    RegimeRunConfig,
    run_once,
)
from shrap.intelligence.regime.classifier import Classification, ClassifierState


class FakeResponse:
    def __init__(self, body: Any) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self._body


class FakeHttpClient:
    def __init__(self, bodies: list[Any]) -> None:
        self._bodies = bodies
        self.urls: list[str] = []

    async def get(self, url: str, headers: dict[str, str]) -> FakeResponse:
        self.urls.append(url)
        return FakeResponse(self._bodies.pop(0))


def _settings() -> AlpacaMarketDataSettings:
    return AlpacaMarketDataSettings(
        api_key="data-key",
        secret_key=SecretStr("data-secret"),
        endpoint="https://data.alpaca.markets",  # type: ignore[arg-type]
    )


def test_market_data_settings_reject_trading_host() -> None:
    with pytest.raises(ValueError, match="data host"):
        AlpacaMarketDataSettings(
            api_key="k",
            secret_key=SecretStr("s"),
            endpoint="https://paper-api.alpaca.markets",  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_get_daily_bars_parses_and_paginates() -> None:
    page_one = {
        "bars": {
            "SPY": [
                {"t": "2026-07-01T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100},
            ]
        },
        "next_page_token": "tok",
    }
    page_two = {
        "bars": {
            "SPY": [
                {"t": "2026-07-02T04:00:00Z", "o": 1.5, "h": 2, "l": 1, "c": 1.8, "v": 90},
            ]
        },
        "next_page_token": None,
    }
    http_client = FakeHttpClient([page_one, page_two])
    client = AlpacaMarketDataClient(_settings())

    bars = await client.get_daily_bars(http_client, ["spy"], "2026-07-01")

    assert len(bars) == 2
    assert bars[0] == DailyBar(
        symbol="SPY", day=date(2026, 7, 1), open=1, high=2, low=0.5, close=1.5, volume=100
    )
    assert "page_token=tok" in http_client.urls[1]
    assert "symbols=SPY" in http_client.urls[0]
    assert "timeframe=1Day" in http_client.urls[0]
    assert "feed=iex" in http_client.urls[0]


@pytest.mark.asyncio
async def test_get_daily_bars_rejects_malformed_shape() -> None:
    http_client = FakeHttpClient([{"bars": [1, 2, 3]}])
    client = AlpacaMarketDataClient(_settings())
    with pytest.raises(ValueError, match="must be an object"):
        await client.get_daily_bars(http_client, ["SPY"], "2026-07-01")


class FakeRedis:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self.calls.append((stream, fields))
        return f"178012860000{len(self.calls)}-0"


class FakeBarSource:
    def __init__(self, bars: list[DailyBar]) -> None:
        self._bars = bars
        self.requested_starts: list[str] = []

    async def get_daily_bars(
        self,
        http_client: Any,
        symbols: list[str],
        start_day: str,
        limit: int = 10000,
    ) -> list[DailyBar]:
        self.requested_starts.append(start_day)
        return self._bars


class FakeOhlcvStore:
    def __init__(self, closes_by_symbol: dict[str, list[float]]) -> None:
        self._closes = closes_by_symbol
        self.upserted: list[DailyBar] = []

    async def upsert_bars(self, bars: list[DailyBar]) -> int:
        self.upserted.extend(bars)
        return len(bars)

    async def closes(self, symbol: str, limit: int = 260) -> list[float]:
        return self._closes.get(symbol, [])

    async def latest_day(self, symbol: str) -> date | None:
        return None


class FakeRegimeStore:
    def __init__(self, state: ClassifierState | None = None) -> None:
        self._state = state
        self.records: list[tuple[str, Classification]] = []

    async def record(
        self,
        event_id: str,
        result: Classification,
        features_payload: dict[str, float | None],
    ) -> None:
        self.records.append((event_id, result))

    async def last_state(self) -> ClassifierState | None:
        return self._state


def _steady_market() -> dict[str, list[float]]:
    steady = [100.0 * (1.001**i) for i in range(260)]
    return {sym: list(steady) for sym in ("SPY", "QQQ", "IWM", "HYG", "TLT")}


@pytest.mark.asyncio
async def test_run_once_publishes_tick_and_sizing_modifier_and_persists() -> None:
    redis = FakeRedis()
    store = FakeOhlcvStore(_steady_market())
    regime_store = FakeRegimeStore()
    config = RegimeRunConfig(symbols=("SPY", "QQQ", "IWM", "HYG", "TLT"))

    result = await run_once(
        bar_source=FakeBarSource([]),
        http_client=None,  # type: ignore[arg-type]
        ohlcv_store=store,
        regime_store=regime_store,
        publisher=EventPublisher(redis),
        config=config,
        today=date(2026, 7, 6),
    )

    streams = [stream for stream, _ in redis.calls]
    assert streams == [STREAM_REGIME_TICK, STREAM_REGIME_SIZING_MODIFIER]

    tick = Envelope.from_redis_fields(normalize_redis_fields(redis.calls[0][1]))
    assert tick.payload is not None
    assert tick.payload["label"] == result.label
    assert tick.payload["missing_features"] == []

    sizing = Envelope.from_redis_fields(normalize_redis_fields(redis.calls[1][1]))
    assert sizing.payload is not None
    assert sizing.payload["band"] == list(result.sizing_band)
    assert sizing.payload["analogs"] == []
    assert tick.correlation_id == sizing.correlation_id

    assert len(regime_store.records) == 1
    assert regime_store.records[0][1].label == result.label


@pytest.mark.asyncio
async def test_run_once_emits_changed_event_after_debounce() -> None:
    redis = FakeRedis()
    store = FakeOhlcvStore(_steady_market())
    # Prior state: melt-up has led for 2 runs against an unknown label.
    regime_store = FakeRegimeStore(
        ClassifierState(label="unknown", leader="late-cycle-melt-up", streak=2)
    )
    config = RegimeRunConfig(symbols=("SPY", "QQQ", "IWM", "HYG", "TLT"), debounce_m=3)

    result = await run_once(
        bar_source=FakeBarSource([]),
        http_client=None,  # type: ignore[arg-type]
        ohlcv_store=store,
        regime_store=regime_store,
        publisher=EventPublisher(redis),
        config=config,
        today=date(2026, 7, 6),
    )

    assert result.changed
    streams = [stream for stream, _ in redis.calls]
    assert streams == [
        STREAM_REGIME_TICK,
        STREAM_REGIME_CHANGED,
        STREAM_REGIME_SIZING_MODIFIER,
    ]
    changed = Envelope.from_redis_fields(normalize_redis_fields(redis.calls[1][1]))
    assert changed.payload is not None
    assert changed.payload["new_label"] == "late-cycle-melt-up"
    assert changed.payload["prior_label"] == "unknown"


@pytest.mark.asyncio
async def test_run_once_with_no_data_emits_unknown_and_flags_missing() -> None:
    redis = FakeRedis()
    store = FakeOhlcvStore({})
    regime_store = FakeRegimeStore()
    config = RegimeRunConfig(symbols=("SPY", "QQQ", "IWM", "HYG", "TLT"))

    result = await run_once(
        bar_source=FakeBarSource([]),
        http_client=None,  # type: ignore[arg-type]
        ohlcv_store=store,
        regime_store=regime_store,
        publisher=EventPublisher(redis),
        config=config,
        today=date(2026, 7, 6),
    )

    assert result.label == "unknown"
    tick = Envelope.from_redis_fields(normalize_redis_fields(redis.calls[0][1]))
    assert tick.payload is not None
    assert len(tick.payload["missing_features"]) == 7


def test_console_script_extra_compose_and_dockerfile_are_wired() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["scripts"]["shrap-regime-classifier"] == (
        "shrap.agents.intelligence.regime_classifier.__main__:main"
    )
    extra = pyproject["project"]["optional-dependencies"]["regime-classifier"]
    assert "asyncpg>=0.29" in extra
    assert "pydantic-settings>=2.4" in extra

    compose = Path("infra/docker-compose.yml").read_text()
    assert "regime-classifier:" in compose
    assert "container_name: shrap_regime_classifier" in compose
    assert "REGIME_CLASSIFIER_REDIS_URL" in compose
    assert "REGIME_CLASSIFIER_POSTGRES_DSN" in compose
    assert "REGIME_CLASSIFIER_ALPACA_API_KEY" in compose

    dockerfile = Path("infra/regime-classifier.Dockerfile").read_text()
    assert 'CMD ["shrap-regime-classifier"]' in dockerfile


def test_settings_symbol_list_requires_primary_credit_rates(monkeypatch: Any) -> None:
    from shrap.agents.intelligence.regime_classifier.config import Settings

    monkeypatch.setenv("REGIME_CLASSIFIER_SYMBOLS", "AAPL,NVDA")
    with pytest.raises(ValueError, match="must include"):
        Settings().symbol_list()

    monkeypatch.setenv("REGIME_CLASSIFIER_SYMBOLS", "SPY,HYG,TLT,AAPL")
    assert Settings().symbol_list() == ("SPY", "HYG", "TLT", "AAPL")


def test_settings_redacted_is_log_safe() -> None:
    from shrap.agents.intelligence.regime_classifier.config import Settings

    settings = Settings(
        postgres_dsn=SecretStr("postgresql://db.internal/shrap"),
        alpaca_api_key="data-key",
        alpaca_secret_key=SecretStr("data-secret"),
    )
    redacted = settings.redacted()
    assert redacted["postgres_dsn"] == "***"
    assert "data-secret" not in str(redacted)
    assert "db.internal" not in str(redacted)
