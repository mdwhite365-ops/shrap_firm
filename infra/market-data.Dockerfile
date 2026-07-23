# Market Data backfill - shared infrastructure, not an always-on agent. Fetches
# historical daily bars from Alpaca (IEX feed, adjustment=all) and upserts them
# into market_data.daily_bars, the Strategy Evaluator's backtest prerequisite.
# Run on demand: `docker compose run --rm market-data shrap-market-data-backfill ...`.

# ---------- builder ----------
FROM python:3.12-slim AS builder
WORKDIR /src

RUN pip install --no-cache-dir --upgrade pip hatchling

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

# ---------- runtime ----------
FROM python:3.12-slim AS runtime

RUN useradd --create-home --uid 10001 shrap
WORKDIR /app

COPY --from=builder /wheels /wheels

RUN pip install --no-cache-dir /wheels/*.whl \
        "httpx>=0.27" \
        "structlog>=24.1" \
        "pydantic>=2.7" \
        "pydantic-settings>=2.4" \
        "asyncpg>=0.29" \
    && rm -rf /wheels

USER shrap

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# No default long-running command: this is a run-to-completion tool. The
# backfill entrypoint is invoked explicitly via `docker compose run`.
CMD ["shrap-market-data-backfill", "--help"]
