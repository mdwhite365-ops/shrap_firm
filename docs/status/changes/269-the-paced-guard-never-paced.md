### The paced guard never paced (#269)

#267 replaced the quota guard's fixed 50-item stride with one derived from
observed cost. **It did not work, and it did not fail loudly.**

The CLI's closure read `len(calls)` from the enclosing scope to learn how many
items had been scored. But `calls` is only extended *after* `run_bar` returns:

```python
bar_calls = await run_bar(...)  # every check happens in here
calls.extend(bar_calls)  # ...and this runs afterwards
```

So during a bar the count never moved. `items_since` was zero at every check,
the `if items_since > 0` measurement branch never executed, and the stride fell
back to `QUOTA_CHECK_MAX` every single time. **A fixed 100-item interval wearing
the costume of an adaptive one** — and at the 0.19%-per-item price of filing
prompts, 100 items is 19% of a session window against a 10% reserve. The same
defect #267 was written to fix, with a larger stride.

Reproduced directly: the old wiring, asked what it could see, returned
`[0, 0, 0, 0]` across a 50-item bar.

It was also observed live and misread. The bar B run made meter reads at items
10, 110 and 210 — a constant stride, reported at the time as evidence the pacing
was working. **Three evenly spaced reads are what a fixed interval looks like.**

### The fix

`QuotaCheck` now takes the scored count as a parameter, because the caller
cannot see it. `run_bar` passes `len(calls)` — its own list, which does grow —
and the closure can no longer read a variable that does not move.

The `paced` baseline is also seeded from the **preflight** meter read taken
before the run starts, so the first mid-run check already has something to
measure against. Previously the first interval was a guess *at the maximum*,
which is the widest possible window for an unexpectedly expensive item to run
unchecked.

A test pins it: the count the guard is told must be strictly increasing, and
**a constant count means nothing is measurable**. That assertion is the one that
would have caught #267 shipping as a no-op.

### The lesson

The same shape as everything else this week: a component reconstructed a fact
the system already held — how much work had been done — and reconstructed it
from a variable that was not being updated. Nothing raised. The guard reported
success, stopped runs, and logged reasons, while measuring a constant.

**A number that never changes is not a measurement.** Assert that it moves.

### And ruff is now pinned, because the gate was not deterministic

This branch failed CI on a branch `make lint` had just passed locally. Not a
mistake either side made: `ruff>=0.6` let CI install **0.16.8** while the
developer ran a cached **0.15.20**, and **0.16 formats Python inside markdown
fences**. CI therefore checked 528 files where local checked 409, and the one
disagreement was a fenced `python` block in this very entry.

A formatter that disagrees with itself across machines turns a green local gate
into a red CI one for reasons nobody can reproduce — and the obvious response,
reformatting until CI is happy, changes files the local tool will change straight
back. `ruff` is pinned to an exact version now. Bump it deliberately, and
reformat in the same commit.

To see CI's view before pushing without touching the pin:
`uvx ruff@latest format --check .`
