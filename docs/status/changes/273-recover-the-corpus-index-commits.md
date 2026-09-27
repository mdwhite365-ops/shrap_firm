### #250's last three commits never reached main (#273)

KI-001's second shape again: pushing to a branch whose PR has already merged.
The push succeeds and GitHub says nothing. Three commits went to
`phase1/qdrant-corpus-index` 11–51 minutes after #250 merged, including
**`c726849` — "the index had no research papers in it — only EDGAR."**

`main` has been indexing `document_text` only, which is filled for EDGAR and
nothing else. The live index held papers anyway, because the Dell's image
happened to be built thirteen seconds after that commit, from the branch. **The
container was right and the repo was wrong**, so no test, no deploy check and no
doc-drift run could see it. The next rebuild from `main` would have quietly
stopped indexing papers under #259's retrieval.

Found by running `git cherry origin/main <branch>` over all 264 remote
branches: ten carry commits not on `main`, and five of those commits have no
patch-equivalent there. #137 was a deliberate close. `57135b4` (#26's regime
floor) is a calibration and went to Mike separately. The other three are these.

**Also fixed: nobody was running it.** The index was tools-only, and the Qdrant
backup was byte-identical for five nights running. It now builds every six
hours as an always-on service, and `research.corpus_index_cursor` is a
freshness target at 72 hours.

**Run the KI-001 check over every branch in a session, not only the ones you
remember touching.** The memory note says so; this is what it costs when the
check isn't run.
