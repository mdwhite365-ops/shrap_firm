# Strategy Evaluator - Research Department. The firm's deterministic gatekeeper:
# walk-forward + realistic costs + friction stress + verdict, promoting a
# hypothesis-stage strategy to paper or killing it on the strategy registry.
# NOT an always-on agent this card (no overnight queue runner) - a run-to-
# completion tool, gated behind the "tools" compose profile like market-data.
# Run on demand:
#   docker compose run --rm strategy-evaluator \
#     shrap-strategy-evaluate --strategy-id 01STRAT... [--dry-run]
# No LLM (the stats core is deterministic; LLMs are excluded from the verdict).
# No broker credentials.

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
        "python-ulid>=2.7" \
        "asyncpg>=0.29" \
        "numpy>=1.26" \
        "pandas>=2.2" \
    && rm -rf /wheels

USER shrap

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# No default long-running command: this is a run-to-completion tool. The
# evaluate entrypoint is invoked explicitly via `docker compose run`.
CMD ["shrap-strategy-evaluate", "--help"]
