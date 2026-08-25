"""The slice of ``httpx.AsyncClient`` the LLM layer actually uses.

Extracted so :mod:`shrap.llm.client` and :mod:`shrap.llm.tracing` can both
depend on it without depending on each other. The client posts completions and
the tracer posts traces; they take the same handle and neither needs to know
the other exists.

These are :class:`typing.Protocol` classes, so nothing has to subclass them —
``httpx.AsyncClient`` satisfies both by shape, and so does a three-line fake in
a test.
"""

from __future__ import annotations

from typing import Any, Protocol


class HTTPResponse(Protocol):
    @property
    def status_code(self) -> int: ...

    def json(self) -> Any: ...

    @property
    def text(self) -> str: ...


class HTTPClient(Protocol):
    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        timeout: float,
        headers: dict[str, str] | None = ...,
    ) -> HTTPResponse: ...


__all__ = ["HTTPClient", "HTTPResponse"]
