"""Paper-strategy runner core — the trading loop's last structural piece.

The runner is the Strategy Fixture's real successor: on each entry into market
phase ``open`` it evaluates every active *paper-stage* strategy and emits a
``trading.strategy.signal`` whenever a strategy's flat/invested target changes.
It emits *signals only* — the Decision Maker -> Pre-Trade Checker -> Execution
chain does the rest. PAPER ONLY: no intents, no broker calls, no real money.

- :mod:`.engine` — the pure ``inputs -> (signals, state writes)`` planner.
- :mod:`.store` — the ``research.strategy_runner_state`` owner.

The service shell lives in :mod:`shrap.agents.research.strategy_runner`.
"""
