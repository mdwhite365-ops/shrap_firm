# Corpus Index - chunks the firm's stored full text, embeds it locally, and
# writes the vectors to Qdrant. Compose runs it as a six-hourly incremental loop. By hand:
#   docker compose run --rm corpus-index shrap-corpus-index build
#
# This is the "full text to Qdrant" leg that docs/02-architecture.md specifies
# for Intelligence and Structural Analysis and that nothing implemented. Qdrant
# ran healthy and empty from 2026-07-02 until this shipped.
#
# Dependencies are the same short list every other tool image carries. No
# qdrant-client and no embedding library: Qdrant is four REST calls over httpx
# (src/shrap/common/qdrant_client.py) and the embeddings come from the Ollama
# already running beside it. Boring beats clever, and it keeps this image small.

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

CMD ["shrap-corpus-index", "--help"]
