# Strategy Evaluator - Research Department. The firm's deterministic gatekeeper:
# walk-forward + realistic costs + friction stress + verdict, promoting a
# hypothesis-stage strategy to paper or killing it on the strategy registry.
#
# ONE image, TWO compose services (both defined in docker-compose.yml):
#   strategy-evaluator          tools profile, run-to-completion, on demand:
#     docker compose --profile tools run --rm strategy-evaluator \
#       shrap-strategy-evaluate --strategy-id 01STRAT... [--dry-run]
#   strategy-evaluator-trigger  always-on, sweeps hypothesis-stage strategies
#     on an interval and evaluates each through the identical pipeline
#     (ADR-0013 item 2). Kills apply unattended; promotes are held for review
#     (ADR-0015).
# A rebuild therefore has to recreate BOTH - see
# docs/runbooks/deploying-after-a-code-change.md.
#
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
        "pydantic-settings>=2.4" \
        "python-ulid>=2.7" \
        "asyncpg>=0.29" \
        "numpy>=1.26" \
        "pandas>=2.2" \
    && rm -rf /wheels

# Card output root. Compose bind-mounts the repo's evaluations directory over
# this path; creating it here (owned by shrap) keeps the image runnable without
# a mount, and makes the ownership requirement explicit rather than implicit in
# WORKDIR. See the strategy-evaluator block in docker-compose.yml.
RUN mkdir -p /cards && chown shrap:shrap /cards

USER shrap

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# No default long-running command. The on-demand service invokes
# shrap-strategy-evaluate explicitly via `docker compose run`; the trigger
# service sets `command:` in compose. Neither relies on this default, which
# exists only so a bare `docker run` of the image explains itself.
CMD ["shrap-strategy-evaluate", "--help"]
