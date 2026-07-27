# Infrastructure Mapper (Month-2) - deterministic, run-on-demand. Loads the
# hand-seeded world-changer graph into research.graphs* and inspects graph state.
# Run: `docker compose run --rm infra-mapper shrap-infra-mapper load-seed-graph`.

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
        "asyncpg>=0.29" \
        "python-ulid>=2.0" \
    && rm -rf /wheels

USER shrap

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Run-to-completion tool; the subcommand is passed explicitly via compose run.
CMD ["shrap-infra-mapper", "list"]
