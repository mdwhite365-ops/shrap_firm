### The quota guard paces itself by observed cost (#268)

#261 re-checked the shared Ollama allowance every 50 items. **A fixed stride only
works if you know the per-item price in advance, and 2026-09-20 disproved that
twice in one night.** The same code cost **0.089%** of a session window per item
scoring summaries and **0.188%** scoring filings — the window is cost-weighted,
and the filing prompts (#266) were 2.5× larger.

At 0.188%, 50 items is **~9.5% of the window**: the check interval was the same
size as the 10% reserve it existed to protect. The guard duly checked at item 200
(~89%, just under the line), did not check again until item 250, and stopped at
**98.9%** — having eaten the entire reserve between two consecutive checks.

`quota_check` now returns a `QuotaDecision` carrying both the stop reason and
**when to ask again**. The CLI derives that interval from the usage delta between
its own last two checks — the only place the price of an item is knowable, since
it depends on the prompt and the prompt depends on the source — and halves it, so
the run notices *before* crossing rather than after. Bounded at 10 items (a meter
read is an HTTP round trip and must not dominate a cheap run) and 100 (never
coast on a stale estimate).

Verified on the run that finished the EDGAR set: 175 items, guard never fired,
session ended at 34.5%.

### And the gap the guard itself created

A clean quota stop deliberately writes nothing for the items behind it —
*"never attempted" is not "failed"* — so `--resume-run`, which looks for errored
rows, could not see them. That left 175 of 425 EDGAR items invisible to the only
mechanism built to pick them up.

`--items-from-run` now composes with `--resume-run`: the target set minus what
the run already holds a verdict for. That is how the remaining 175 were finished,
healing run `01M2YR69J0ZB5JPF742Y92ZQGC` to all 425 rows in place.

**A guard that stops safely is only half a guard.** The other half is being able
to continue.
