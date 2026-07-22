"""Alpaca news-API client (``/v1beta1/news``).

Read-only against the Alpaca *data* host, same auth headers and credential
handling as :mod:`shrap.intelligence.market_data` — keys arrive only through
env-backed :class:`AlpacaMarketDataSettings`, are never logged, and the
endpoint is pinned to the data host. Free with the existing paper-account
credentials (Benzinga-sourced); no new vendor.

Follows ``next_page_token`` pagination until the feed is exhausted, oldest
first, so the caller's cursor advances forward in time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from shrap.intelligence.market_data import AlpacaMarketDataSettings
from shrap.trading_floor.alpaca import AsyncHttpClient

NEWS_PATH = "/v1beta1/news"
NEWS_SOURCE = "alpaca-news"


@dataclass(frozen=True, slots=True)
class NewsItem:
    """One Alpaca news item, normalized for storage and scoring."""

    item_id: str
    headline: str
    summary: str | None
    author: str | None
    url: str | None
    news_source: str | None
    symbols: tuple[str, ...]
    published_at: datetime | None
    updated_at: datetime | None
    payload: dict[str, Any] = field(default_factory=dict)


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_news_item(entry: object) -> NewsItem:
    """Normalize one raw Alpaca news object into a :class:`NewsItem`."""

    if not isinstance(entry, dict):
        raise ValueError("Alpaca news entry must be an object")
    raw_id = entry.get("id")
    if raw_id is None:
        raise ValueError("Alpaca news entry lacks an id")
    headline = entry.get("headline")
    if not isinstance(headline, str) or not headline.strip():
        raise ValueError(f"Alpaca news entry {raw_id} lacks a headline")
    raw_symbols = entry.get("symbols")
    symbols: tuple[str, ...] = ()
    if isinstance(raw_symbols, list):
        symbols = tuple(s.strip().upper() for s in raw_symbols if isinstance(s, str) and s.strip())
    summary = entry.get("summary")
    author = entry.get("author")
    url = entry.get("url")
    news_source = entry.get("source")
    return NewsItem(
        item_id=str(raw_id),
        headline=headline.strip(),
        summary=summary.strip() if isinstance(summary, str) and summary.strip() else None,
        author=author.strip() if isinstance(author, str) and author.strip() else None,
        url=url.strip() if isinstance(url, str) and url.strip() else None,
        news_source=(
            news_source.strip() if isinstance(news_source, str) and news_source.strip() else None
        ),
        symbols=symbols,
        published_at=_parse_ts(entry.get("created_at")),
        updated_at=_parse_ts(entry.get("updated_at")),
        payload=entry,
    )


class AlpacaNewsClient:
    """Fetch news items from Alpaca's data API (``/v1beta1/news``)."""

    def __init__(self, settings: AlpacaMarketDataSettings) -> None:
        self._settings = settings

    def _auth_headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self._settings.api_key,
            "APCA-API-SECRET-KEY": self._settings.secret_key.get_secret_value(),
        }

    def _base(self) -> str:
        return str(self._settings.endpoint).rstrip("/")

    async def get_news(
        self,
        http_client: AsyncHttpClient,
        symbols: list[str],
        start: str,
        limit: int = 50,
    ) -> list[NewsItem]:
        """Fetch news for ``symbols`` since ``start`` (RFC3339), oldest first.

        Follows ``next_page_token`` pagination until exhausted. Malformed
        entries raise ``ValueError`` — the bias is to fail the fetch loudly,
        never to silently drop items from the denominator.
        """

        symbol_param = ",".join(sorted({s.strip().upper() for s in symbols if s.strip()}))
        items: list[NewsItem] = []
        if not symbol_param:
            return items
        page_token: str | None = None
        while True:
            url = (
                f"{self._base()}{NEWS_PATH}"
                f"?symbols={symbol_param}&start={start}&limit={limit}"
                f"&sort=asc&include_content=false&exclude_contentless=false"
            )
            if page_token:
                url += f"&page_token={page_token}"
            response = await http_client.get(url, headers=self._auth_headers())
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError("Alpaca news response must be a JSON object")
            raw_news = body.get("news") or []
            if not isinstance(raw_news, list):
                raise ValueError("Alpaca news response 'news' must be an array")
            for entry in raw_news:
                items.append(parse_news_item(entry))
            token = body.get("next_page_token")
            if not token:
                return items
            page_token = str(token)


__all__ = [
    "NEWS_PATH",
    "NEWS_SOURCE",
    "AlpacaNewsClient",
    "NewsItem",
    "parse_news_item",
]
