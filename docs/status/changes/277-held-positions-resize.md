### Held positions resize toward target (#277)

The 2026-09-18 exposure ruling (paper stage 0.25 → 0.80) reached only new
buys. The Runner acted on flat↔invested transitions and never touched a held
position, so five sessions later the momentum account held six names at ~$190
(the old size) beside three at ~$600 (the new one), and was ~29% invested
against a ~60% target.

A held position now gets topped up or trimmed when it drifts more than 25%
(and $25) from its effective target. That target is the full-scale entry
multiplied by **the scale the Risk Officer recorded** on the account's newest
approved buy. The Runner reads the scale rather than computing stage × regime ×
posterior itself, because a second copy of the Officer's sizing rule is exactly
the kind of reconstruction this project keeps catching disagreeing with the
original. A top-up requests gap ÷ scale, so the Officer's scaling lands the
fill on the gap. Sells are unscaled, so a trim sells the excess directly.

When a ruling changes a size, ask what already holds the old size. A rule that
only runs on a transition never reaches anything that isn't changing.
