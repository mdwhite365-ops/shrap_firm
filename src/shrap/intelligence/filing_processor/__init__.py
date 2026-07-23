"""Filing Processor — Intelligence Department 8-K deep-read agent.

Per docs/agents/intelligence/filing-processor.md (seed scope): poll the Tech
Watcher's ``research.raw_source_items`` table for ``source = 'sec-edgar'`` /
``kind = '8-K'`` rows, resolve each to the Tier 3 roster by CIK, fetch the full
filing text from EDGAR Archives, split it by declared 8-K item code, bulk-score
each item for materiality on the local-classification tier, escalate material
items to cloud-default, and publish materiality>=1 signals on
``intelligence.signal``. Every Tier 3-matched filing lands in
``intelligence.filings`` (the denominator); every verdict appends to
``intelligence.filing_verdict_history`` (the KI-007 calibration log).

Reads the Tech Watcher's table but never writes it, and keeps its own poll
cursor separate from the Tech Watcher's ingest cursor. Plain asyncio service
loop; no LangGraph. The domain logic here is wrapped by the thin agent package
``shrap.agents.intelligence.filing_processor``.
"""
