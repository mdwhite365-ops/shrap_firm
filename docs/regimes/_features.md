# Regime feature definitions (v0, proxy set)

**Owner:** Intelligence Department (Regime Classifier)
**Status:** Implemented (v0 proxy set)
**Date:** 2026-07-06
**Source of truth for formulas:** `src/shrap/intelligence/regime/features.py`

The Regime Classifier spec references this file for feature formulas. The v0
set is a **proxy set**: Alpaca's free IEX feed carries no index data
(VIX, MOVE, DXY) and no consolidated breadth, so the features below stand in
for the markers the regime cards quote. Known bias: IEX is thinner than SIP,
so realized-vol reads high relative to published SPX figures — thresholds in
`profiles.py` are calibrated to the proxy, not to the cards' literal ranges.

All features are computed from daily closes in `market_data.ohlcv_1d`.
Missing or insufficient data yields a missing feature (`None`) — never a
silent interpolation. A condition on a missing feature does not pass.

| Feature | Definition | Proxy for |
|---|---|---|
| `vol_20d` | Annualized stdev of last 20 daily log returns of SPY (× √252) | SPX realized vol / VIX level |
| `vol_trend` | `realized_vol(5d) / realized_vol(60d)` on SPY; >1 = vol rising | VIX term structure direction |
| `pct_above_200dma` | SPY last close / SMA200 − 1 | Index trend extension |
| `trend_50_200` | SPY SMA50 / SMA200 − 1 | Trend strength (golden-cross style) |
| `breadth_above_200dma` | Fraction of tracked symbols above their own SMA200 | Breadth (% above 200dma) |
| `dispersion_20d` | Cross-sectional stdev of 20-day simple returns across tracked symbols (≥3 required) | Sector dispersion |
| `credit_hyg_tlt_20d` | HYG 20-day return − TLT 20-day return | Credit spreads (HY vs duration) |

Tracked symbol set is `REGIME_CLASSIFIER_SYMBOLS`
(default `SPY,QQQ,IWM,HYG,TLT,AAPL,NVDA,TSLA,LMT`); SPY is the primary/index
proxy, HYG/TLT the credit pair.

## Deferred (needs a paid or additional data source)

- True VIX level and VIX1/VIX3 term structure
- MOVE (rate vol), DXY (dollar trend)
- Consolidated advance/decline and new highs/lows breadth
- ADX-based trend strength

When any of these land, add the feature to `features.py`, extend this table,
and recalibrate the affected profile thresholds by PR.
