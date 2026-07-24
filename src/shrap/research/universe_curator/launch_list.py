"""The locked Tier 3 launch list (DQ-004, locked by Mike 2026-07-23).

The 50 names live here as a code constant so the launch load is deterministic
and idempotent. ``docs/universe/README.md`` remains ground truth: a test
(``tests/research/test_universe_launch_list.py``) parses the ticker tables in
that document and asserts this constant matches it exactly. When Mike revises
the list, the README changes first (drift updates the spec, not the code) and
this constant follows.

Categories mirror the six README sections. They are carried for audit legibility
only — tier state does not branch on category. CIKs are populated for the four
names the Filing Processor roster already knows (EDGAR resolution is CIK-based);
every other CIK is ``None`` and backfilled later, never guessed. ``profile_path``
is set for the six names with a seed profile under ``docs/universe/``; ``None``
marks a grandfathered name whose profile backfill is pending (Mike's ruling
2026-07-23: the 44 unprofiled launch names load anyway; the profile prerequisite
applies only to future Tier 2 promotions).
"""

from __future__ import annotations

from typing import NamedTuple

# Category keys, one per README section header.
CATEGORY_ETF = "liquid-etf"
CATEGORY_MEGA_CAP_TECH = "mega-cap-tech"
CATEGORY_HIGH_RETAIL = "high-retail-interest"
CATEGORY_DEFENSE = "defense"
CATEGORY_MID_CAP = "liquid-mid-cap"
CATEGORY_CRYPTO = "crypto"


class LaunchName(NamedTuple):
    """One Tier 3 launch member: its ticker and its README category."""

    ticker: str
    category: str


# The four CIKs the Filing Processor roster already carries (verified against
# src/shrap/intelligence/filing_processor/config.py:_DEFAULT_ROSTER). All other
# launch names carry no CIK — backfill later, never guess.
LAUNCH_CIKS: dict[str, str] = {
    "AAPL": "320193",
    "NVDA": "1045810",
    "TSLA": "1318605",
    "LMT": "936468",
}

# The six names with a seed behavioral profile under docs/universe/. Every other
# launch name is grandfathered with profile_path = None (Mike, 2026-07-23).
LAUNCH_PROFILE_PATHS: dict[str, str] = {
    "SPY": "docs/universe/spy.md",
    "QQQ": "docs/universe/qqq.md",
    "TSLA": "docs/universe/tsla.md",
    "NVDA": "docs/universe/nvda.md",
    "AAPL": "docs/universe/aapl.md",
    "LMT": "docs/universe/lmt.md",
}

# In README document order. The cross-check test asserts this ordering and the
# per-category membership against docs/universe/README.md.
LAUNCH_LIST: tuple[LaunchName, ...] = (
    # Liquid ETFs — 12 names (regime expression and hedging)
    LaunchName("SPY", CATEGORY_ETF),
    LaunchName("QQQ", CATEGORY_ETF),
    LaunchName("IWM", CATEGORY_ETF),
    LaunchName("DIA", CATEGORY_ETF),
    LaunchName("XLE", CATEGORY_ETF),
    LaunchName("XLF", CATEGORY_ETF),
    LaunchName("XLK", CATEGORY_ETF),
    LaunchName("XLI", CATEGORY_ETF),
    LaunchName("XLV", CATEGORY_ETF),
    LaunchName("GLD", CATEGORY_ETF),
    LaunchName("TLT", CATEGORY_ETF),
    LaunchName("UUP", CATEGORY_ETF),
    # Mega-cap tech and growth leaders — 8 names
    LaunchName("AAPL", CATEGORY_MEGA_CAP_TECH),
    LaunchName("MSFT", CATEGORY_MEGA_CAP_TECH),
    LaunchName("GOOGL", CATEGORY_MEGA_CAP_TECH),
    LaunchName("META", CATEGORY_MEGA_CAP_TECH),
    LaunchName("AMZN", CATEGORY_MEGA_CAP_TECH),
    LaunchName("NVDA", CATEGORY_MEGA_CAP_TECH),
    LaunchName("AMD", CATEGORY_MEGA_CAP_TECH),
    LaunchName("AVGO", CATEGORY_MEGA_CAP_TECH),
    # High-retail-interest — 10 names (trap setup priority)
    LaunchName("TSLA", CATEGORY_HIGH_RETAIL),
    LaunchName("PLTR", CATEGORY_HIGH_RETAIL),
    LaunchName("GME", CATEGORY_HIGH_RETAIL),
    LaunchName("AMC", CATEGORY_HIGH_RETAIL),
    LaunchName("COIN", CATEGORY_HIGH_RETAIL),
    LaunchName("RIVN", CATEGORY_HIGH_RETAIL),
    LaunchName("MSTR", CATEGORY_HIGH_RETAIL),
    LaunchName("SOFI", CATEGORY_HIGH_RETAIL),
    LaunchName("HOOD", CATEGORY_HIGH_RETAIL),
    LaunchName("NIO", CATEGORY_HIGH_RETAIL),
    # Defense contractors — 6 names (government-contract intelligence)
    LaunchName("LMT", CATEGORY_DEFENSE),
    LaunchName("RTX", CATEGORY_DEFENSE),
    LaunchName("NOC", CATEGORY_DEFENSE),
    LaunchName("GD", CATEGORY_DEFENSE),
    LaunchName("LHX", CATEGORY_DEFENSE),
    LaunchName("LDOS", CATEGORY_DEFENSE),
    # Liquid mid-caps — 10 names (catalyst trading and dispersion)
    LaunchName("MU", CATEGORY_MID_CAP),
    LaunchName("MRVL", CATEGORY_MID_CAP),
    LaunchName("CRWD", CATEGORY_MID_CAP),
    LaunchName("NET", CATEGORY_MID_CAP),
    LaunchName("SNOW", CATEGORY_MID_CAP),
    LaunchName("DKNG", CATEGORY_MID_CAP),
    LaunchName("ROKU", CATEGORY_MID_CAP),
    LaunchName("AFRM", CATEGORY_MID_CAP),
    LaunchName("U", CATEGORY_MID_CAP),
    LaunchName("PYPL", CATEGORY_MID_CAP),
    # Crypto exposure — 4 names (small allocation)
    LaunchName("IBIT", CATEGORY_CRYPTO),
    LaunchName("ETHA", CATEGORY_CRYPTO),
    LaunchName("MARA", CATEGORY_CRYPTO),
    LaunchName("RIOT", CATEGORY_CRYPTO),
)

# The Tier 3 hard cap at launch (ADR-0012). The launch list fills it exactly.
TIER3_CAP = 50


__all__ = [
    "CATEGORY_CRYPTO",
    "CATEGORY_DEFENSE",
    "CATEGORY_ETF",
    "CATEGORY_HIGH_RETAIL",
    "CATEGORY_MEGA_CAP_TECH",
    "CATEGORY_MID_CAP",
    "LAUNCH_CIKS",
    "LAUNCH_LIST",
    "LAUNCH_PROFILE_PATHS",
    "TIER3_CAP",
    "LaunchName",
]
