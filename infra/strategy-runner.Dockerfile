# Strategy Runner - Research Department. The paper-trading loop's last
# structural piece: on each entry into market phase `open` it evaluates every
# active paper-stage strategy and emits a trading.strategy.signal on a target
# transition. Long-running service (NOT a tools-profile run-to-completion job).
# Emits signals only - no intents, no broker credentials, PAPER ONLY.

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
        "structlog>=24.1" \
        "pydantic>=2.7" \
        "pydantic-settings>=2.4" \
        "python-ulid>=2.7" \
        "asyncpg>=0.29" \
        "numpy>=1.26" \
        "pandas>=2.2" \
        "pandas-market-calendars>=4.4" \
    && rm -rf /wheels

USER shrap

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["shrap-strategy-runner"]
