"""Dry-run fundamentals specs. Fundamentals come from SEC into memory; nothing is written."""
import asyncio, os, sys
from pathlib import Path
import httpx
from shrap.common.db import create_asyncpg_pool
from shrap.market_data.fundamentals import parse_fundamentals
from shrap.market_data.fundamentals_backfill import fund_tickers
from shrap.market_data.shares import company_facts_url
from shrap.market_data.shares_backfill import load_ticker_cik_map
from shrap.research.strategy_evaluator.engine import EvalConfig
from shrap.research.strategy_evaluator.pipeline import EvaluationPipeline
from shrap.research.strategy_evaluator.store import PostgresEvaluatorReader
from shrap.research.strategy_seed.spec_strategies import load_documents, spec_record

UA = "shrap-firm research mdwhite365@gmail.com"

class SecReader(PostgresEvaluatorReader):
    cache = None
    async def read_fundamentals(self, tickers):
        if SecReader.cache is None:
            out = {}
            async with httpx.AsyncClient() as http:
                ciks = await load_ticker_cik_map(http, user_agent=UA, timeout=30)
                for t in sorted(set(tickers) - fund_tickers()):
                    cik = ciks.get(t)
                    if not cik: continue
                    r = await http.get(company_facts_url(cik), headers={"User-Agent": UA}, timeout=30)
                    if r.status_code == 200:
                        for row in parse_fundamentals(r.json(), ticker=t, cik=cik):
                            out.setdefault(t, {}).setdefault(row.metric, []).append((row.filed_at, row.period_end, row.value))
                    await asyncio.sleep(0.2)
            SecReader.cache = out
            print(f"[fetched fundamentals for {len(out)} names]")
        return SecReader.cache

class MemRegistry:
    def __init__(self, records): self.r = {x.strategy_id: x for x in records}
    async def get(self, sid): return self.r.get(sid)
    async def lineage(self, sid): return [self.r[sid]]

class Nope:
    def __getattr__(self, name): raise RuntimeError(f"dry run touched {name}")

async def main(path):
    from dataclasses import replace
    records = [spec_record(d, strategy_id=f"DRYF{i:02d}") for i, d in enumerate(load_documents(Path(path).read_text()))]
    if os.environ.get("EQUITIES_ONLY"):
        eq = sorted(set(records[0].tickers["long"]) - fund_tickers())
        records = [replace(r, tickers={"long": eq, "short": []}) for r in records]
        print(f"[universe and benchmark restricted to {len(eq)} operating companies]")
    pool = await create_asyncpg_pool(os.environ["STRATEGY_EVALUATOR_POSTGRES_DSN"])
    pipe = EvaluationPipeline(registry=MemRegistry(records), reader=SecReader(pool), store=Nope(),
                              publisher=Nope(), config=EvalConfig(), card_root=Path("/tmp/cards"))
    for rec in records:
        try:
            out = await pipe.evaluate(rec.strategy_id, trigger="dry-run")
            am = out.active_metrics or {}
            print(f"{rec.name[:58]:<59} {out.verdict:<14} {out.reason:<30} IR={am.get('information_ratio')!s:<8.8} "
                  f"sharpe={out.base_sharpe:+.2f} stress={out.stress_sharpe:+.2f} trades={out.total_trades} {out.reported_consistency}")
        except Exception as exc:
            print(f"{rec.name[:58]:<59} ERROR {exc!r}"[:300])
    await pool.close()

asyncio.run(main(sys.argv[1]))
