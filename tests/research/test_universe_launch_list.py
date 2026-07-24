"""Launch-list cross-check against docs/universe/README.md, plus load idempotency.

The README ticker tables are ground truth (DQ-004, locked 2026-07-23). This test
parses those tables and asserts ``LAUNCH_LIST`` matches them exactly — ordering,
per-category membership, and counts — so the code constant can never silently
drift from the document Mike locked.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from shrap.research.universe_curator.curator import (
    LAUNCH_EVIDENCE_REF,
    MECHANISM_MIKE_SEED,
    STREAM_PROMOTED,
    load_launch_list,
)
from shrap.research.universe_curator.launch_list import (
    CATEGORY_CRYPTO,
    CATEGORY_DEFENSE,
    CATEGORY_ETF,
    CATEGORY_HIGH_RETAIL,
    CATEGORY_MEGA_CAP_TECH,
    CATEGORY_MID_CAP,
    LAUNCH_CIKS,
    LAUNCH_LIST,
    LAUNCH_PROFILE_PATHS,
    TIER3_CAP,
)
from shrap.research.universe_curator.store import TIER_ACTIVE

README = Path(__file__).resolve().parents[2] / "docs" / "universe" / "README.md"

# Header substring → category key. The README section headers are prose; this
# mapping is the one piece of README knowledge that lives in the test.
_HEADER_CATEGORY = {
    "Liquid ETFs": CATEGORY_ETF,
    "Mega-cap tech": CATEGORY_MEGA_CAP_TECH,
    "High-retail-interest": CATEGORY_HIGH_RETAIL,
    "Defense contractors": CATEGORY_DEFENSE,
    "Liquid mid-caps": CATEGORY_MID_CAP,
    "Crypto exposure": CATEGORY_CRYPTO,
}


def _parse_readme() -> list[tuple[str, str]]:
    """Return (ticker, category) pairs in README document order."""

    pairs: list[tuple[str, str]] = []
    current: str | None = None
    for raw in README.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("###"):
            current = None
            # A category header looks like "### <name> — N names ...".
            if "names" in line:
                for substr, category in _HEADER_CATEGORY.items():
                    if substr in line:
                        current = category
                        break
            continue
        if current is not None and line.startswith("|"):
            cell = line.strip("|").split("|", 1)[0].strip()
            if not cell or cell.lower() == "ticker" or set(cell) <= {"-", ":"}:
                continue
            pairs.append((cell, current))
    return pairs


def test_launch_list_matches_readme_exactly() -> None:
    readme_pairs = _parse_readme()
    constant_pairs = [(n.ticker, n.category) for n in LAUNCH_LIST]
    assert constant_pairs == readme_pairs, "LAUNCH_LIST drifted from docs/universe/README.md"


def test_launch_list_counts() -> None:
    assert len(LAUNCH_LIST) == TIER3_CAP == 50
    counts = {
        CATEGORY_ETF: 12,
        CATEGORY_MEGA_CAP_TECH: 8,
        CATEGORY_HIGH_RETAIL: 10,
        CATEGORY_DEFENSE: 6,
        CATEGORY_MID_CAP: 10,
        CATEGORY_CRYPTO: 4,
    }
    for category, expected in counts.items():
        got = sum(1 for n in LAUNCH_LIST if n.category == category)
        assert got == expected, f"{category}: expected {expected}, got {got}"


def test_launch_tickers_unique() -> None:
    tickers = [n.ticker for n in LAUNCH_LIST]
    assert len(tickers) == len(set(tickers))


def test_known_ciks_and_profiles_are_launch_members() -> None:
    members = {n.ticker for n in LAUNCH_LIST}
    assert set(LAUNCH_CIKS) <= members
    assert set(LAUNCH_PROFILE_PATHS) <= members
    # Only the four roster CIKs are known; everything else backfills later.
    assert LAUNCH_CIKS == {
        "AAPL": "320193",
        "NVDA": "1045810",
        "TSLA": "1318605",
        "LMT": "936468",
    }
    # Exactly the six seed-profiled names.
    assert set(LAUNCH_PROFILE_PATHS) == {"SPY", "QQQ", "TSLA", "NVDA", "AAPL", "LMT"}


class FakeRedis:
    def __init__(self) -> None:
        self.streams: list[str] = []
        self.payloads: list[dict[str, Any]] = []

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        import json

        self.streams.append(stream)
        self.payloads.append(json.loads(fields["payload"]))
        return f"{len(self.streams)}-0"


class FakeLaunchStore:
    def __init__(self) -> None:
        self.tiers: dict[str, dict[str, Any]] = {}

    async def get_tier_row(self, ticker: str) -> dict[str, Any] | None:
        row = self.tiers.get(ticker)
        return dict(row) if row is not None else None

    async def upsert_active(
        self,
        *,
        ticker: str,
        cik: str | None,
        mechanism: str,
        evidence_ref: str,
        entered_at: datetime,
        profile_path: str | None,
    ) -> None:
        self.tiers[ticker] = {
            "ticker": ticker,
            "cik": cik,
            "tier": TIER_ACTIVE,
            "mechanism": mechanism,
            "evidence_ref": evidence_ref,
            "entered_at": entered_at,
            "expiry": None,
            "falsifier": None,
            "profile_path": profile_path,
        }


async def test_load_launch_list_promotes_all_fifty_with_mike_seed() -> None:
    store = FakeLaunchStore()
    redis = FakeRedis()

    loaded = await load_launch_list(store, redis)  # type: ignore[arg-type]

    assert len(loaded) == 50
    assert len(store.tiers) == 50
    assert redis.streams == [STREAM_PROMOTED] * 50
    for payload in redis.payloads:
        assert payload["mechanism"] == MECHANISM_MIKE_SEED
        assert payload["evidence_ref"] == LAUNCH_EVIDENCE_REF
        assert payload["source_tier"] == "discovery"
        assert payload["destination_tier"] == TIER_ACTIVE
    # CIK populated only for the four known; NULL for the rest.
    assert store.tiers["AAPL"]["cik"] == "320193"
    assert store.tiers["MSFT"]["cik"] is None
    # profile_path set for the six seed-profiled names; None (grandfathered) else.
    assert store.tiers["SPY"]["profile_path"] == "docs/universe/spy.md"
    assert store.tiers["MSFT"]["profile_path"] is None


async def test_load_launch_list_is_idempotent() -> None:
    store = FakeLaunchStore()
    redis = FakeRedis()
    await load_launch_list(store, redis)  # type: ignore[arg-type]

    redis2 = FakeRedis()
    loaded = await load_launch_list(store, redis2)  # type: ignore[arg-type]

    assert loaded == []
    assert redis2.streams == []
    assert len(store.tiers) == 50
