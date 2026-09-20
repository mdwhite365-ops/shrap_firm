### Resume is a single-bar operation, and now says so (#268)

`--resume-run` subtracts the items a run already holds a verdict for from the
items it was meant to cover. **That state is per item, not per (bar, item)** —
`load_corpus` returns one list that every bar then scores, and
`SELECT_RUN_SCORED_ITEM_IDS_SQL` does not filter by bar.

So resuming two bars at once would subtract the items bar B had already scored
from bar C's set. C would silently skip them and its report would describe a
smaller run than B's **while claiming to be the same comparison** — the whole
point of running bars over one item set being that the item set is identical.

It now refuses, with the reason. Refusing is better than a resume that quietly
returns a different corpus per bar, and it costs nothing: the three bars have
always been run as separate invocations anyway.

Found while planning the re-run of bars B and C on filings (#266), which spans
several session windows and therefore depends entirely on resume being correct.

Selector validation moved out of `main()` into `_validate_selectors`, so the
refusals are testable without running the CLI. `--report-only` takes no item
selector, for the same reason.
