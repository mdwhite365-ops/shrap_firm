### #26's regime floor never reached main (#275)

The commit that set the crisis-recovery `vol_20d` floor to 0.18, adjoining
the melt-up ceiling, went to its branch nine minutes after #26 merged. For
eleven weeks `main` has carried a 0.18–0.20 band in which no regime
qualifies, above a comment saying the gap was chosen on purpose, while the
commit that removed it sat on a merged branch.

It has cost nothing so far. `vol_20d` has stayed between 0.069 and 0.131 for
the last thirty days. This PR is the original commit, recovered for a ruling:
merge it to adjoin the bands, or close it to keep the gap. Either answer ends
the ambiguity.
