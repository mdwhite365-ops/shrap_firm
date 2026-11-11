### The status docs, reconciled at #293 (#294)

`session-handoff.md`, `implementation-timeline.md` and `CLAUDE.md` were 9, 17
and 14 PRs behind `main`: the fifth time this set has drifted. The handoff
still named the 2026-09-23 audit as the place to pick up, six weeks and one
week-long trading halt later.

All three now carry #277–#293: the strategy factory, the `min_trades` ruling
(#287), the split that restated ETHA's history (#290), and the two kill-switch
latches (#291, #293). The timeline has a new ordered card list.

**Written from the repo, not the Dell.** Nothing about what is running was
re-measured. The handoff lists what to check first, starting with whether #291
is deployed and the accounts are buying again.

**Merging this = accepting the new card order**, which puts verifying the firm
is trading, and scheduling `make firm-health`, ahead of any research card.
