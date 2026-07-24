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

from datetime import date

from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.market_data.store import DailyBarRow
from shrap.trading_floor.alpaca import AsyncHttpClient

# Feed / adjustment defaults, surfaced as constants so the store's ``source``
# provenance and the URL query string never drift apart.
IEX_FEED = "iex"
ADJUSTMENT_ALL = "all"


def source_label(feed: str) -> str:
    """Provenance string for the store's ``source`` column, e.g. ``alpaca-iex``."""

    return f"alpaca-{feed}"


class AlpacaDailyBarsClient:
    """Read-only historical daily bars from Alpaca's data API."""

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
        rows: list[DailyBarRow] = []
        if not symbol:
            return rows
        page_token: str | None = None
        while True:
            url = (
                f"{self._base()}/v2/stocks/bars"
                f"?symbols={symbol}&timeframe=1Day"
                f"&start={start_day}&end={end_day}"
                f"&limit={limit}&adjustment={self._adjustment}&feed={self._feed}&sort=asc"
            )
            if page_token:
                url += f"&page_token={page_token}"
            response = await http_client.get(url, headers=self._auth_headers())
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Alpaca bars response must be a JSON object")
            raw_bars = body.get("bars") or {}
            if not isinstance(raw_bars, dict):
                raise ValueError("Alpaca bars response 'bars' must be an object")
            for entries in raw_bars.values():
                if not isinstance(entries, list):
                    raise ValueError(f"Alpaca bars for {symbol} must be an array")
                for entry in entries:
                    rows.append(self._parse_bar(symbol, entry))
            token = body.get("next_page_token")
            if not token:
                return rows
            page_token = str(token)

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


def _opt_int(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) else None


def _opt_float(value: object) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


__all__ = [
    "ADJUSTMENT_ALL",
    "IEX_FEED",
    "AlpacaDailyBarsClient",
    "source_label",
]
