### The 2026-09-23 audit's doc set (#276)

`make doc-drift` was green at #270 while CLAUDE.md made seven false claims:
the filter model, the docker group, the container count, the fixture's state,
the index size, the index's consumers, and the literature count. It also runs
`sudo` in a Makefile target that no longer needs it. The drift check compares
PR numbers, and every one of these files had been touched recently, so recency
said nothing about truth. That is the 2026-08-04 lesson again.

The handoff now opens with the audit's findings, the four actions only Mike can
take, and a measured answer to whether the firm is researching: 15 of 16
strategies ever were Mike-seeded, there were no evaluations between 07-31 and
09-14, and the last week admitted 0 of 1,284 arXiv items.
