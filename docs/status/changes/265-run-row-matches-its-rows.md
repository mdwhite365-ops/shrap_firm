### The run row must agree with the rows it summarises (#265)

#261 made `bar_experiment_results` upsert-safe for a resume and left
`bar_experiment_runs` a plain `INSERT`. So the first real resume, on 2026-09-20,
scored all 407 outstanding items, persisted every one of them, and **then died on
a primary-key violation** writing the run row.

The results table was correct: 599 scored, 0 errors, 3 admits. The summary row
still said **`1 admit, 407 errors`** — frozen from the run that had failed.

**That is a summary table disagreeing with its own detail table**, which is the
error that made #255 claim bars B and C had never run and then made #260 blame
the wrong cause for it. Twice in one day from reading a summary, and then a third
time by writing one.

Two fixes, because there were two defects:

- **`INSERT_RUN_SQL` now upserts.** `finished_at` and `report_markdown` refresh;
  `started_at` deliberately does not, because the run started when it started and
  rewriting it would falsify the record.
- **The summary is now derived from the stored rows, not from process memory.**
  A resume only holds the items it re-scored — 407 of 599 — so a report built
  from `calls` would have described a smaller run than the table beside it even
  with the upsert working. `build_report` reads the results back and summarises
  those, which makes the run row and the results rows the same fact by
  construction rather than by agreement.

`--report-only RUN_ID` rebuilds a run's summary from its stored results and
rewrites the row. It makes **no completions** and spends no allowance; the model
is read from the existing row so a repair cannot relabel which model produced the
verdicts. Used to repair run `01M2YHEGZ5KSBAADGHYK96QGAY`.
