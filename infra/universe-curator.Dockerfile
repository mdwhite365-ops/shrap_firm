# Universe Curator - Research Department. Owns Tier 2 (Watch) and Tier 3
# (Active) universe state under ADR-0012: the research.universe_tiers and
# research.universe_staging stores, the five tier-transition events, and the
# shrap-universe-promote approval CLI (Mike's decision surface). The service
# container runs the daily watch-expiry sweep; the CLI is exec'd into it for
# seed / stage / approve / reject / extend / expire / load-launch-list. No LLM,
# no broker credentials. Sole writer of the tier tables; the Pre-Trade Checker
# is a read-only consumer.

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
        "redis>=5.0" \
        "httpx>=0.27" \
        "structlog>=24.1" \
        "pydantic>=2.7" \
        "pydantic-settings>=2.4" \
        "asyncpg>=0.29" \
    && rm -rf /wheels

USER shrap

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["shrap-universe-curator"]
