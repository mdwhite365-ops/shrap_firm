"""Tech Watcher — top of the Framework #1 research funnel (ADR-0007).

Slice A (this package's first card): deterministic source ingest only.
Per-source cursor advance, raw item persistence to
``research.raw_source_items``, ``ingestion.heartbeat`` per source, and
single-source failure isolation. No LLM anywhere in this slice — the bulk
filter and synthesis passes are the next card.
"""
