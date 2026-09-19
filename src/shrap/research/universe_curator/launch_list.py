"""The locked Tier 3 launch list (DQ-004, locked by Mike 2026-07-23).

The 50 names live here as a code constant so the launch load is deterministic
and idempotent. ``docs/universe/README.md`` remains ground truth: a test
(``tests/research/test_universe_launch_list.py``) parses the ticker tables in
that document and asserts this constant matches it exactly. When Mike revises
the list, the README changes first (drift updates the spec, not the code) and
this constant follows.

Categories mirror the six README sections. They are carried for audit legibility
only — tier state does not branch on category. CIKs are populated for the 42
names SEC's own registry resolves (EDGAR resolution is CIK-based); the other
eight are ETF trusts that registry does not key by ticker, and they stay
``None`` rather than being guessed — see :data:`LAUNCH_CIKS_UNRESOLVED`. This
list is also the single source for the Filing Processor's roster, via
:func:`roster_env_value`, so the two cannot drift apart again. ``profile_path``
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


# Resolved 2026-09-18 from SEC's own ``company_tickers.json``, fetched with the
# firm's existing SEC User-Agent. Not hand-entered and not guessed: the file is
# the registry EDGAR itself publishes, and the resolution is reproducible from
# ``LAUNCH_LIST`` alone.
#
# This is the backfill the original note promised ("backfill later, never
# guess"). Until it happened the Filing Processor matched 8-Ks against four
# names out of fifty and dropped the rest at ``roster.ticker_for(cik)``.
#
# EIGHT NAMES ARE DELIBERATELY ABSENT — QQQ, IWM, XLE, XLF, XLK, XLI, XLV, TLT.
# Each is an ETF whose trust files under a registrant name that does not carry
# the ticker in that file, so no entry can be made without guessing which
# registrant is meant. They stay out, exactly as the original rule says. The
# cost is nil in practice: an index ETF does not file the material-event 8-Ks
# this roster exists to catch. Note that SPY, DIA, GLD, UUP, IBIT and ETHA are
# also ETFs and *did* resolve — they are kept because resolving them cost
# nothing, not because they are expected to file.
LAUNCH_CIKS: dict[str, str] = {
    "SPY": "884394",  # SPDR S&P 500 ETF TRUST
    "DIA": "1041130",  # SPDR DOW JONES INDUSTRIAL AVERAGE ETF TRUST
    "GLD": "1222333",  # SPDR GOLD TRUST
    "UUP": "1383151",  # Invesco DB US Dollar Index Bullish Fund
    "AAPL": "320193",  # Apple Inc.
    "MSFT": "789019",  # MICROSOFT CORP
    "GOOGL": "1652044",  # Alphabet Inc.
    "META": "1326801",  # Meta Platforms, Inc.
    "AMZN": "1018724",  # AMAZON COM INC
    "NVDA": "1045810",  # NVIDIA CORP
    "AMD": "2488",  # ADVANCED MICRO DEVICES INC
    "AVGO": "1730168",  # Broadcom Inc.
    "TSLA": "1318605",  # Tesla, Inc.
    "PLTR": "1321655",  # Palantir Technologies Inc.
    "GME": "1326380",  # GameStop Corp.
    "AMC": "1411579",  # AMC ENTERTAINMENT HOLDINGS, INC.
    "COIN": "1679788",  # Coinbase Global, Inc.
    "RIVN": "1874178",  # Rivian Automotive, Inc. / DE
    "MSTR": "1050446",  # Strategy Inc
    "SOFI": "1818874",  # SoFi Technologies, Inc.
    "HOOD": "1783879",  # Robinhood Markets, Inc.
    "NIO": "1736541",  # NIO Inc.
    "LMT": "936468",  # LOCKHEED MARTIN CORP
    "RTX": "101829",  # RTX Corp
    "NOC": "1133421",  # NORTHROP GRUMMAN CORP /DE/
    "GD": "40533",  # GENERAL DYNAMICS CORP
    "LHX": "202058",  # L3HARRIS TECHNOLOGIES, INC. /DE/
    "LDOS": "1336920",  # Leidos Holdings, Inc.
    "MU": "723125",  # MICRON TECHNOLOGY INC
    "MRVL": "1835632",  # Marvell Technology, Inc.
    "CRWD": "1535527",  # CrowdStrike Holdings, Inc.
    "NET": "1477333",  # Cloudflare, Inc.
    "SNOW": "1640147",  # Snowflake Inc.
    "DKNG": "1883685",  # DraftKings Inc.
    "ROKU": "1428439",  # ROKU, INC
    "AFRM": "1820953",  # Affirm Holdings, Inc.
    "U": "1810806",  # Unity Software Inc.
    "PYPL": "1633917",  # PayPal Holdings, Inc.
    "IBIT": "1980994",  # iShares Bitcoin Trust ETF
    "ETHA": "2000638",  # iShares Ethereum Trust ETF
    "MARA": "1507605",  # MARA Holdings, Inc.
    "RIOT": "1167419",  # Riot Platforms, Inc.
}

# The tickers with no CIK, stated rather than left to be inferred from absence.
# A reader asking "why is XLF not matching filings" should find the answer here
# rather than concluding the roster is broken.
LAUNCH_CIKS_UNRESOLVED: frozenset[str] = frozenset(
    {"QQQ", "IWM", "XLE", "XLF", "XLK", "XLI", "XLV", "TLT"}
)


def roster_env_value() -> str:
    """``LAUNCH_CIKS`` as the ``TICKER:CIK`` string the Filing Processor parses.

    Derived rather than duplicated. The roster and the launch list were two
    hand-maintained lists that silently disagreed for eight weeks — four names
    against fifty — and the only way that does not recur is for one of them to
    be computed from the other.

    Sorted so the value is stable: an env string that reordered between runs
    would show as a config change in every diff of a deployed container.
    """

    return ",".join(f"{ticker}:{LAUNCH_CIKS[ticker]}" for ticker in sorted(LAUNCH_CIKS))


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
    "LAUNCH_CIKS_UNRESOLVED",
    "LAUNCH_LIST",
    "LAUNCH_PROFILE_PATHS",
    "TIER3_CAP",
    "LaunchName",
    "roster_env_value",
]
