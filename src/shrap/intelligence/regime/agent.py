"""Regime Classifier agent — one pass and the service loop.

Each run: sync recent daily bars from Alpaca's data API, load closes from
market_data.ohlcv_1d, compute the deterministic feature vector, classify
with hysteresis/debounce, publish the intel.regime.* events, and persist
the run. The historical-analog LLM layer is intentionally absent (Month 2
scope; feature-flagged for Month 3).
"""

from __future__ import annotations

import asyncio
import signal
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol, cast

import httpx
import structlog
from redis.asyncio import Redis
from ulid import ULID

from shrap.common.db import create_asyncpg_pool
from shrap.common.logging import configure_logging
from shrap.events import EventPublisher, RedisPublisher
from shrap.intelligence.market_data import (
    AlpacaMarketDataClient,
    AlpacaMarketDataSettings,
    DailyBar,
    PostgresOhlcvStore,
)
from shrap.intelligence.regime.classifier import Classification, ClassifierState, classify
from shrap.intelligence.regime.features import FeatureVector, compute_features
from shrap.intelligence.regime.profiles import DEFAULT_PROFILES, RegimeProfile
from shrap.intelligence.regime.store import PostgresRegimeStore
from shrap.trading_floor.alpaca import AsyncHttpClient

log = structlog.get_logger(__name__)

STREAM_REGIME_TICK = "intel.regime.tick"
STREAM_REGIME_CHANGED = "intel.regime.changed"
STREAM_REGIME_SIZING_MODIFIER = "intel.regime.sizing-modifier"
SCHEMA_VERSION = "1.0.0"
PRODUCED_BY = "intelligence/regime-classifier"


class BarSource(Protocol):
    async def get_daily_bars(
        self,
        http_client: AsyncHttpClient,
        symbols: list[str],
        start_day: str,
        limit: int = 10000,
    ) -> list[DailyBar]: ...


class OhlcvStore(Protocol):
    async def upsert_bars(self, bars: list[DailyBar]) -> int: ...

    async def closes(self, symbol: str, limit: int = 260) -> list[float]: ...

    async def latest_day(self, symbol: str) -> date | None: ...


class RegimeStore(Protocol):
    async def record(
        self,
        event_id: str,
        result: Classification,
        features_payload: dict[str, float | None],
    ) -> None: ...

    async def last_state(self) -> ClassifierState | None: ...


class Publisher(Protocol):
    async def publish(
        self,
        stream: str,
        produced_by: str,
        schema_version: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class RegimeRunConfig:
    """Symbols and knobs for one classification pass."""

    symbols: tuple[str, ...]
    primary_symbol: str = "SPY"
    credit_symbol: str = "HYG"
    rates_symbol: str = "TLT"
    lookback_days: int = 400
    debounce_m: int = 3
    epsilon: float = 0.05
    profiles: tuple[RegimeProfile, ...] = DEFAULT_PROFILES


async def sync_bars(
    bar_source: BarSource,
    http_client: AsyncHttpClient,
    store: OhlcvStore,
    config: RegimeRunConfig,
    today: date,
) -> int:
    """Pull daily bars since the last stored day (or the full lookback)."""

    latest = await store.latest_day(config.primary_symbol)
    if latest is None:
        start = today - timedelta(days=config.lookback_days)
    else:
        start = latest  # re-pull the last stored day; upsert makes this idempotent
    bars = await bar_source.get_daily_bars(
        http_client,
        symbols=list(config.symbols),
        start_day=start.isoformat(),
    )
    if bars:
        await store.upsert_bars(bars)
    return len(bars)


async def load_features(store: OhlcvStore, config: RegimeRunConfig) -> FeatureVector:
    closes_by_symbol: dict[str, Sequence[float]] = {}
    for symbol in config.symbols:
        closes_by_symbol[symbol] = await store.closes(symbol, limit=260)
    primary = closes_by_symbol.get(config.primary_symbol, [])
    hyg = closes_by_symbol.get(config.credit_symbol, [])
    tlt = closes_by_symbol.get(config.rates_symbol, [])
    return compute_features(
        primary_closes=primary,
        closes_by_symbol=closes_by_symbol,
        hyg_closes=hyg,
        tlt_closes=tlt,
    )


async def run_once(
    bar_source: BarSource,
    http_client: AsyncHttpClient,
    ohlcv_store: OhlcvStore,
    regime_store: RegimeStore,
    publisher: Publisher,
    config: RegimeRunConfig,
    today: date,
    produced_by: str = PRODUCED_BY,
) -> Classification:
    """One full pass: sync → features → classify → publish → persist."""

    synced = await sync_bars(bar_source, http_client, ohlcv_store, config, today)
    features = await load_features(ohlcv_store, config)
    prior = await regime_store.last_state() or ClassifierState()
    result = classify(
        features=features,
        profiles=config.profiles,
        prior=prior,
        debounce_m=config.debounce_m,
        epsilon=config.epsilon,
    )
    run_id = str(ULID())
    features_payload = features.as_payload()

    tick_payload: dict[str, Any] = {
        "label": result.label,
        "confidence": result.confidence,
        "changed": result.changed,
        "leader": result.leader,
        "streak": result.streak,
        "features": features_payload,
        "missing_features": result.missing_features,
        "bars_synced": synced,
    }
    await publisher.publish(
        stream=STREAM_REGIME_TICK,
        produced_by=produced_by,
        schema_version=SCHEMA_VERSION,
        payload=tick_payload,
        correlation_id=run_id,
    )

    if result.changed:
        await publisher.publish(
            stream=STREAM_REGIME_CHANGED,
            produced_by=produced_by,
            schema_version=SCHEMA_VERSION,
            payload={
                "prior_label": result.prior_label,
                "new_label": result.label,
                "streak": result.streak,
                "confidence": result.confidence,
                "features": features_payload,
            },
            correlation_id=run_id,
        )
        log.info(
            "regime_classifier.changed",
            prior_label=result.prior_label,
            new_label=result.label,
            streak=result.streak,
        )

    await publisher.publish(
        stream=STREAM_REGIME_SIZING_MODIFIER,
        produced_by=produced_by,
        schema_version=SCHEMA_VERSION,
        payload={
            "label": result.label,
            "confidence": result.confidence,
            "band": [result.sizing_band[0], result.sizing_band[1]],
            "derivation": "hand-authored v0 per-regime band (profiles.py); analog layer absent",
            "analogs": [],
        },
        correlation_id=run_id,
    )

    await regime_store.record(run_id, result, features_payload)
    log.info(
        "regime_classifier.tick",
        label=result.label,
        confidence=result.confidence,
        leader=result.leader,
        streak=result.streak,
        missing_features=result.missing_features,
        bars_synced=synced,
        features=features_payload,
        profile_scores={
            score.name: ("PASS" if score.qualifies else "fail")
            + f" {score.soft_hits}/{score.soft_total} soft"
            for score in result.scores
        },
    )
    return result


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass


async def _interruptible_sleep(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        pass


async def run(
    redis_url: str,
    postgres_dsn: str,
    market_data_settings: AlpacaMarketDataSettings,
    config: RegimeRunConfig,
    service_name: str = "regime-classifier",
    log_level: str = "INFO",
    interval_seconds: float = 300.0,
    retry_delay_seconds: float = 60.0,
) -> None:
    """Run the Regime Classifier service until SIGINT/SIGTERM."""

    configure_logging(service_name, log_level)
    log.info(
        "regime_classifier.starting",
        redis_url=redis_url,
        postgres_dsn="***",
        alpaca=market_data_settings.redacted(),
        symbols=list(config.symbols),
        interval_seconds=interval_seconds,
    )
    stop = asyncio.Event()
    _install_signal_handlers(stop)
    redis: Redis = Redis.from_url(redis_url, decode_responses=True, socket_timeout=30)
    pool = await create_asyncpg_pool(postgres_dsn)
    ohlcv_store = PostgresOhlcvStore(pool)
    await ohlcv_store.ensure_schema()
    regime_store = PostgresRegimeStore(pool)
    await regime_store.ensure_schema()
    bar_source = AlpacaMarketDataClient(market_data_settings)
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        try:
            while not stop.is_set():
                try:
                    await run_once(
                        bar_source=bar_source,
                        http_client=cast(AsyncHttpClient, http_client),
                        ohlcv_store=ohlcv_store,
                        regime_store=regime_store,
                        publisher=EventPublisher(cast(RedisPublisher, redis)),
                        config=config,
                        today=date.today(),
                    )
                    delay = interval_seconds
                except Exception:
                    log.exception("regime_classifier.pass_failed")
                    delay = retry_delay_seconds
                await _interruptible_sleep(stop, delay)
        finally:
            await redis.aclose()
            await pool.close()
            log.info("regime_classifier.stopped")


__all__ = [
    "PRODUCED_BY",
    "SCHEMA_VERSION",
    "STREAM_REGIME_CHANGED",
    "STREAM_REGIME_SIZING_MODIFIER",
    "STREAM_REGIME_TICK",
    "RegimeRunConfig",
    "load_features",
    "run",
    "run_once",
    "sync_bars",
]
