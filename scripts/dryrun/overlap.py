"""How much of MACD-top-10 is just 126/21 momentum? Reads only."""
import asyncio, os, statistics
from datetime import date
from shrap.common.db import create_asyncpg_pool
from shrap.research.strategy_evaluator.store import PostgresEvaluatorReader
from shrap.research.strategy_evaluator.strategy import PricePanel
from shrap.research.strategy_evaluator.cross_sectional import CrossSectionalMomentumStrategy
from shrap.research.strategy_evaluator.signals import SignalSpecStrategy
from shrap.research.strategy_seed.technical_strategies import _MOMENTUM_TICKERS

async def main():
    pool = await create_asyncpg_pool(os.environ["STRATEGY_EVALUATOR_POSTGRES_DSN"])
    r = PostgresEvaluatorReader(pool)
    bars = {t: await r.read_bars(t, date(2015,1,1), date.today(), "all") for t in _MOMENTUM_TICKERS}
    panel = PricePanel.from_bars({t:b for t,b in bars.items() if b}, await r.read_shares(list(bars)))
    mom = CrossSectionalMomentumStrategy.from_spec({"lookback":126,"skip":21,"top_n":10})
    macd = SignalSpecStrategy.from_spec({"signal":{"feature":"macd"},"top_n":10,"require_sign":True})
    overlaps=[]; mret=[]; oret=[]
    tick=panel.tickers
    for i in range(200, panel.n_bars-1):
        w=panel.window(i)
        a={t for t,v in macd.target_weights(w).items() if v>0}
        b={t for t,v in mom.target_weights(w).items() if v>0}
        if a and b: overlaps.append(len(a&b)/max(len(a),1))
        def ret(sel):
            rs=[panel.closes[t][i+1]/panel.closes[t][i]-1 for t in sel if panel.is_live(t,i) and panel.is_live(t,i+1)]
            return sum(rs)/len(rs) if rs else 0.0
        live=[t for t in tick if panel.is_live(t,i) and panel.is_live(t,i+1)]
        bench=ret(live)
        mret.append(ret(a)-bench); oret.append(ret(b)-bench)
    corr=statistics.correlation(mret,oret)
    print(f"days={len(mret)} mean overlap of MACD book with momentum book={statistics.mean(overlaps):.1%}")
    print(f"correlation of daily active returns MACD vs momentum={corr:+.2f}")
    await pool.close()
asyncio.run(main())
