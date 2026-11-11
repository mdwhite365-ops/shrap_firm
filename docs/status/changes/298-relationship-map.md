### Measured relationships between assets (#298)

ADR-0018 card 8, the measuring half. `shrap-relationships` reports, for each
driver (XLE, TLT, UUP, GLD, IBIT by default) against each launch-list name, the
correlation of daily returns **with SPY removed from both sides**, and how many
non-overlapping 63-session windows agree in sign with the whole span.

The window agreement is the point. A whole-span correlation that was strongly
negative in some quarters and positive in others is not something a plan can
lean on, and one number hides that. A link is `stable` only at |corr| of 0.30 or
more with 80% of windows agreeing.

**Read-only, no model, not yet run against the database.** It measures same-day
co-movement, not one asset leading another. XLE is oil companies and stands in
for oil, since the firm ingests no crude price. Nothing consults the report when
a plan is written; for now a person reads it.
