"""Dry-run the Evaluator on spec documents without registering them. Reads only."""
import asyncio, json, os, sys
from pathlib import Path
from shrap.common.db import create_asyncpg_pool
from shrap.research.strategy_evaluator.engine import EvalConfig
from shrap.research.strategy_evaluator.pipeline import EvaluationPipeline
from shrap.research.strategy_evaluator.store import PostgresEvaluatorReader
from shrap.research.strategy_seed.spec_strategies import load_documents, spec_record


class MemRegistry:
    def __init__(self, records): self.r = {x.strategy_id: x for x in records}
    async def get(self, sid): return self.r.get(sid)
    async def lineage(self, sid): return [self.r[sid]]
    async def transition(self, *a, **k): raise RuntimeError("dry run")


class Nope:
    def __getattr__(self, name): raise RuntimeError(f"dry run touched {name}")


async def main(path):
    docs = load_documents(Path(path).read_text())
    records = [spec_record(d, strategy_id=f"DRY{i:02d}") for i, d in enumerate(docs)]
    pool = await create_asyncpg_pool(os.environ["STRATEGY_EVALUATOR_POSTGRES_DSN"])
    pipe = EvaluationPipeline(registry=MemRegistry(records), reader=PostgresEvaluatorReader(pool),
                              store=Nope(), publisher=Nope(), config=EvalConfig(), card_root=Path("/tmp/cards"))
    for rec in records:
        try:
            out = await pipe.evaluate(rec.strategy_id, trigger="dry-run")
            am = out.active_metrics or {}
            print(f"{rec.name[:55]:<56} {out.verdict:<14} {out.reason:<32} IR={am.get('information_ratio')!s:<8.8} "
                  f"sharpe={out.base_sharpe:+.2f} stress={out.stress_sharpe:+.2f} trades={out.total_trades} "
                  f"{out.reported_consistency}")
        except Exception as exc:
            print(f"{rec.name[:55]:<56} ERROR {exc!r}"[:300])
    await pool.close()

asyncio.run(main(sys.argv[1]))
