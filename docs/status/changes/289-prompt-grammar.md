### The proposer's prompt shows the parser's own grammar (#289)

Prompt v4 (#284) described one-operand ops under `args`, and the parser takes them under `of`, so every Hypothesis Generator formula using `abs`, `neg`, `log`, `rank` or `zscore` was refused. v5 renders real example trees that a test parses. A leading negation now folds into the selection for identity, because `top` of `-x` is `bottom` of `x`. The live model also proved unstable on direction: the same paper gave opposite strategies across runs.
