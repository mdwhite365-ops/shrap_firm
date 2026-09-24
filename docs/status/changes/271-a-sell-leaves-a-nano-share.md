### A sell left one nano-share behind (#271)

Found by the 2026-09-23 audit, chasing a `BELOW_BROKER_MINIMUM` veto that fired
3–5 times every session. The vetoed exits were for **1e-09 shares** of TSLA,
QQQ, AVGO and GD on `PA3KQN57WVXY`.

The Execution Agent floored the order quantity with
`math.floor(q * 1e9) / 1e9`, and the multiply is not exact: `0.531726136 * 1e9`
is `531726135.99999994`. So the order went out one nano-share short of the
holding. The fill history shows exactly that — TSLA bought 0.531726136 on
2026-09-08 and sold 0.531726135 the next day; QQQ bought …878, sold …877.
Quantities whose multiply happened to land cleanly (0.26655709) closed fully,
which is why only some names were left with dust.

**The fix truncates `Decimal(repr(q))`.** `repr` is the shortest decimal that
round-trips the float, which is the string the broker sent, so truncating it
loses nothing real and still never rounds up. The Risk Officer's
`quantize_down` had the same arithmetic and got the same fix.

**The dust already at the broker stays until cleared by hand** in the Alpaca
dashboard: AVGO, GD, QQQ, TSLA at 1e-09 and U at 0.012648483 (the last one is
#196's, not this bug's). Positions under $1 cannot be sold through the API.
