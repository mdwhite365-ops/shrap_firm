"""Alpaca daily-bars fetch for the historical backfill (IEX feed, ``adjustment=all``).

Reuses the existing data-host client conventions rather than duplicating them:
credentials, host-only validation, and the auth-header shape come from
:class:`shrap.intelligence.market_data.AlpacaMarketDataSettings` (the same
``ALPACA_API_KEY`` / ``ALPACA_SECRET_KEY`` / ``ALPACA_DATA_ENDPOINT`` env names
the Regime Classifier and News Analyzer already use), and the HTTP surface is
:class:`shrap.trading_floor.alpaca.AsyncHttpClient`.

Two deliberate differences from the Regime Classifier's live client:

- **One ticker per call.** The backfill fetches a single symbol at a time so
  per-ticker progress (rows fetched, date span) is loggable and a polite delay
  can sit between tickers. The Regime Classifier batches symbols because it
  wants one small recent window across the whole set.
- **``adjustment=all``.** Splits *and* dividends, the correct basis for
  backtesting total return. The live classifier uses ``split`` only.

Credential values are never logged or interpolated into any log line; the
auth headers are built at request time and handed straight to the HTTP client.

**IEX, not SIP (recorded project fact).** ``feed=iex`` is the free tier. Its
volumes are a fraction of the SIP consolidated tape, so volume — and any
volatility derived from it — reads above what a live desk on SIP would see.
Thresholds calibrated on this data do not transfer 1:1 to SIP. See
``docs/infrastructure/market-data.md``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from urllib.parse import urlencode

from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.market_data.store import DailyBarRow, IntradayBarRow
from shrap.trading_floor.alpaca import AsyncHttpClient

# Feed / adjustment defaults, surfaced as constants so the store's ``source``
# provenance and the URL query string never drift apart.
IEX_FEED = "iex"
ADJUSTMENT_ALL = "all"

# Alpaca timeframe tokens. 1Min is the finest grain the API offers and the one
# ADR-0016's intraday equities path is scoped against.
TIMEFRAME_1DAY = "1Day"
TIMEFRAME_1MIN = "1Min"


def source_label(feed: str) -> str:
    """Provenance string for the store's ``source`` column, e.g. ``alpaca-iex``."""

    return f"alpaca-{feed}"


class _AlpacaBarsClientBase:
    """Shared credentials, host resolution and pagination for the bars endpoint.

    Both grains hit ``/v2/stocks/bars`` with an identical query shape and differ
    only in ``timeframe`` and how a row is parsed. Sharing the loop rather than
    copying it is deliberate: it carries the ``urlencode`` fix below, which is
    the kind of bug that produces silently short data rather than an error.
    """

    def __init__(
        self,
        settings: AlpacaMarketDataSettings,
        *,
        feed: str = IEX_FEED,
        adjustment: str = ADJUSTMENT_ALL,
    ) -> None:
        self._settings = settings
        self._feed = feed
        self._adjustment = adjustment

    def _auth_headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._settings.api_key,
            "APCA-API-SECRET-KEY": self._settings.secret_key.get_secret_value(),
        }

    def _base(self) -> str:
        return str(self._settings.endpoint).rstrip("/")

    @property
    def source(self) -> str:
        return source_label(self._feed)

    async def _fetch_entries(
        self,
        http_client: AsyncHttpClient,
        symbol: str,
        *,
        timeframe: str,
        start: str,
        end: str,
        limit: int,
    ) -> list[object]:
        """Page through every bar for ``symbol`` in ``[start, end]``, oldest first."""

        entries: list[object] = []
        page_token: str | None = None
        while True:
            # urlencode rather than interpolation, for `page_token` above
            # all: Alpaca's tokens are base64-ish and a raw `+` in a query
            # string decodes to a space, which would end pagination mid-
            # backfill with no error to see. The dates here are safe as
            # written; the news client's timestamps were not, and that bug
            # cost that service its entire deployed life (2026-07-30).
            params: dict[str, str] = {
                "symbols": symbol,
                "timeframe": timeframe,
                "start": str(start),
                "end": str(end),
                "limit": str(limit),
                "adjustment": self._adjustment,
                "feed": self._feed,
                "sort": "asc",
            }
            if page_token:
                params["page_token"] = page_token
            url = f"{self._base()}/v2/stocks/bars?{urlencode(params)}"
            response = await http_client.get(url, headers=self._auth_headers())
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Alpaca bars response must be a JSON object")
            raw_bars = body.get("bars") or {}
            if not isinstance(raw_bars, dict):
                raise ValueError("Alpaca bars response 'bars' must be an object")
            for symbol_entries in raw_bars.values():
                if not isinstance(symbol_entries, list):
                    raise ValueError(f"Alpaca bars for {symbol} must be an array")
                entries.extend(symbol_entries)
            token = body.get("next_page_token")
            if not token:
                return entries
            page_token = str(token)


class AlpacaIntradayBarsClient(_AlpacaBarsClientBase):
    """Read-only historical intraday bars from Alpaca's data API.

    **Extended-hours bars are returned and stored, not filtered.** Alpaca emits
    1Min bars across the extended session, and on IEX those bars are thin to the
    point of being a different instrument. Storing them anyway is the recoverable
    choice: a consumer that wants regular hours writes a ``bar_ts`` predicate,
    whereas bars discarded at ingest are gone. Any strategy that trades this data
    must decide explicitly which session it is trading — silence is not a default
    here, it is an unstated assumption.
    """

    async def get_intraday_bars(
        self,
        http_client: AsyncHttpClient,
        ticker: str,
        start: str,
        end: str,
        *,
        timeframe: str = TIMEFRAME_1MIN,
        limit: int = 10000,
    ) -> list[IntradayBarRow]:
        """Fetch one ticker's intraday bars over ``[start, end]``.

        Bounds accept a plain date or a full RFC-3339 timestamp; Alpaca treats
        a bare date as the start of that day, UTC.
        """

        symbol = ticker.strip().upper()
        if not symbol:
            return []
        entries = await self._fetch_entries(
            http_client, symbol, timeframe=timeframe, start=start, end=end, limit=limit
        )
        return [self._parse_intraday_bar(symbol, timeframe, entry) for entry in entries]

    def _parse_intraday_bar(self, ticker: str, timeframe: str, entry: object) -> IntradayBarRow:
        if not isinstance(entry, dict):
            raise ValueError(f"Alpaca bar entry for {ticker} must be an object")
        timestamp = str(entry.get("t", ""))
        if len(timestamp) < 10:
            raise ValueError(f"Alpaca bar entry for {ticker} lacks a timestamp")
        return IntradayBarRow(
            ticker=ticker,
            bar_ts=_parse_timestamp(timestamp),
            timeframe=timeframe,
            open=float(entry["o"]),
            high=float(entry["h"]),
            low=float(entry["l"]),
            close=float(entry["c"]),
            volume=float(entry["v"]),
            trade_count=_opt_int(entry.get("n")),
            vwap=_opt_float(entry.get("vw")),
            adjustment=self._adjustment,
            source=self.source,
        )


class AlpacaDailyBarsClient(_AlpacaBarsClientBase):
    """Read-only historical daily bars from Alpaca's data API."""

    async def get_daily_bars(
        self,
        http_client: AsyncHttpClient,
        ticker: str,
        start_day: str,
        end_day: str,
        *,
        limit: int = 10000,
    ) -> list[DailyBarRow]:
        """Fetch one ticker's daily bars over ``[start_day, end_day]`` (YYYY-MM-DD).

        Both bounds are inclusive per Alpaca's ``start``/``end`` semantics.
        Follows ``next_page_token`` pagination until the feed reports no more
        pages. Returns rows oldest-first, tagged with this client's feed and
        adjustment provenance.
        """

        symbol = ticker.strip().upper()
        if not symbol:
            return []
        entries = await self._fetch_entries(
            http_client,
            symbol,
            timeframe=TIMEFRAME_1DAY,
            start=start_day,
            end=end_day,
            limit=limit,
        )
        return [self._parse_bar(symbol, entry) for entry in entries]

    def _parse_bar(self, ticker: str, entry: object) -> DailyBarRow:
        if not isinstance(entry, dict):
            raise ValueError(f"Alpaca bar entry for {ticker} must be an object")
        timestamp = str(entry.get("t", ""))
        if len(timestamp) < 10:
            raise ValueError(f"Alpaca bar entry for {ticker} lacks a timestamp")
        return DailyBarRow(
            ticker=ticker,
            session_date=date.fromisoformat(timestamp[:10]),
            open=float(entry["o"]),
            high=float(entry["h"]),
            low=float(entry["l"]),
            close=float(entry["c"]),
            volume=float(entry["v"]),
            trade_count=_opt_int(entry.get("n")),
            vwap=_opt_float(entry.get("vw")),
            adjustment=self._adjustment,
            source=self.source,
        )


def _parse_timestamp(value: str) -> datetime:
    """Alpaca stamps bars RFC-3339 with a trailing ``Z``, which pre-3.11 fromisoformat rejects.

    Normalised rather than sliced: the offset carries which minute this bar
    belongs to, and dropping it would silently shift every bar to whatever the
    reading process considers local.
    """

    normalised = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalised)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _opt_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _opt_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


__all__ = [
    "ADJUSTMENT_ALL",
    "IEX_FEED",
    "TIMEFRAME_1DAY",
    "TIMEFRAME_1MIN",
    "AlpacaDailyBarsClient",
    "AlpacaIntradayBarsClient",
    "source_label",
]
