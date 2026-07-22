"""News Analyzer — Intelligence Department news feed agent.

Per docs/agents/intelligence/news-analyzer.md (seed scope): pull the Alpaca
news flow for the Tier 3 launch names, bulk-score each item for materiality
on the local-classification tier, escalate material items to cloud-default,
and publish materiality>=1 signals on ``intelligence.signal``. Every fetched
item lands in ``intelligence.news_items`` (the denominator) and every verdict
appends to ``intelligence.news_verdict_history`` (the KI-007 calibration log).

Plain asyncio service loop; no LangGraph. The domain logic here is wrapped by
the thin agent package ``shrap.agents.intelligence.news_analyzer``.
"""
