### Event plans: the engine (#297)

ADR-0018 cards 3 to 5. A plan is a written rule that scales new buys down or
pauses them, over a **window** for a scheduled event or for a **hold** after a
ticker's last completed session moves by a threshold. Hand-written through
`shrap-event-plan`; no model drafts one.

**Off unless `PRE_TRADE_CHECKER_EVENT_PLANS_ENFORCEMENT=true`.** Merging changes
no size.

**A plan has no `active` flag.** Its effect is computed from its row and the
clock each time a buy is sized, so nothing has to end one and nothing can fail
to. #291 and #293 were each a release with no caller; this has no release.

Three things accepted on merge:

- **scale-buys also trims held positions**, once a buy is approved under the
  plan, because the Runner's resize (#277) reads a ratio the plan is inside.
- **An unreadable plan table refuses buys.** Exits are never affected.
- The caps: 14-day window, 5-day hold, 366 days armed.

**Not run against Postgres or Redis.** 50 tests through fakes. **Nothing yet
posts an activation to the alert channel**: it is logged and published on a
stream with no consumer, so `shrap-event-plan show` is how to see what is in
force. That is the next card. Triggers read completed sessions only.
