"""Split a document into overlapping windows an embedding model can hold.

Pure functions, no I/O — the same split the rest of the firm uses so this is
testable without Qdrant, Ollama or a database.

**Why chunking is not optional here.** The firm's filings average **408 KB of
text each** (169 filings, 69 MB) because an 8-K carries its exhibits. Embedding
models do not truncate politely at a useful boundary: ``nomic-embed-text`` takes
8192 tokens and silently drops the rest, so a whole-document embedding would be
an embedding of the cover page and the beginning of exhibit 99.1 — a vector that
retrieves confidently and means nothing.

**Characters, not tokens.** A tokenizer would be more precise and would add a
dependency plus a model-specific coupling; the window below is sized well inside
the model's limit so precision buys nothing. This is the boring choice on
purpose.

**Overlap exists because meaning straddles boundaries.** A sentence split across
two chunks appears in neither as a whole, so a query matching that sentence
matches nothing. The overlap is the cheapest fix and costs only storage.
"""

from __future__ import annotations

from collections.abc import Iterator

# ~500 tokens at the usual 4-chars-per-token rule of thumb, comfortably inside
# nomic-embed-text's 8192-token window. Deliberately far below the limit: the
# ratio is an average, and a table of numbers tokenizes much worse than prose.
DEFAULT_CHUNK_CHARS = 2000

# 10% of the window. Enough that a sentence crossing a boundary survives whole
# in one of the two chunks; small enough that the index does not grow by a
# meaningful fraction.
DEFAULT_OVERLAP_CHARS = 200

# Below this a chunk is not worth a vector — a trailing fragment of whitespace
# and a page number retrieves noise.
MIN_CHUNK_CHARS = 50


def chunk_text(
    text: str,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """Overlapping windows over ``text``, in order.

    Returns ``[]`` for text that is empty or only whitespace — a document with
    nothing in it should produce no vectors rather than one meaningless vector.
    """

    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    if overlap_chars < 0:
        raise ValueError("overlap_chars must not be negative")
    if overlap_chars >= chunk_chars:
        # Otherwise the window never advances and this loops forever. Raising
        # beats the alternative: an indexer that hangs with the CPU pinned and
        # no error is indistinguishable from one doing useful work.
        raise ValueError("overlap_chars must be smaller than chunk_chars")

    cleaned = text.strip()
    if not cleaned:
        return []

    step = chunk_chars - overlap_chars
    chunks: list[str] = []
    start = 0
    while start < len(cleaned):
        window = cleaned[start : start + chunk_chars].strip()
        if len(window) >= MIN_CHUNK_CHARS or (not chunks and window):
            # The `not chunks` clause keeps a short document indexable: a
            # 40-character news headline is legitimate content, and dropping it
            # for being under the floor would make short items unsearchable.
            chunks.append(window)
        if start + chunk_chars >= len(cleaned):
            break
        start += step
    return chunks


def iter_chunks(
    text: str,
    *,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> Iterator[tuple[int, str]]:
    """``(index, chunk)`` pairs, for callers that need the position.

    The index is stored on every point so a retrieved passage can say *where* in
    a 400 KB filing it came from. Without it a hit cites a document and leaves
    the reader to search it by hand.
    """

    yield from enumerate(chunk_text(text, chunk_chars=chunk_chars, overlap_chars=overlap_chars))


def estimate_chunks(total_chars: int, *, chunk_chars: int = DEFAULT_CHUNK_CHARS) -> int:
    """Rough chunk count for a corpus of ``total_chars``.

    Used to print an estimate before a long indexing run. Ignores overlap and
    per-document boundaries, so it under-counts slightly — stated rather than
    corrected, because its only job is to answer "minutes or hours?".
    """

    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be positive")
    return max(0, total_chars) // chunk_chars


__all__ = [
    "DEFAULT_CHUNK_CHARS",
    "DEFAULT_OVERLAP_CHARS",
    "MIN_CHUNK_CHARS",
    "chunk_text",
    "estimate_chunks",
    "iter_chunks",
]
