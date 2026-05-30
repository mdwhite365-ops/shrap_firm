# Health Monitor - first agent. Multi-stage: build a wheel of the shrap
# package, install it lean in the runtime stage as a non-root user.

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

# Install shrap wheel + runtime deps + health-monitor optional dep.
RUN pip install --no-cache-dir /wheels/*.whl \
        "redis>=5.0" \
        "httpx>=0.27" \
        "structlog>=24.1" \
        "pydantic>=2.7" \
        "python-ulid>=2.7" \
        "pydantic-settings>=2.4" \
    && rm -rf /wheels

USER shrap

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD ["shrap-health-monitor"]
