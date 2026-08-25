"""The one interface an agent needs from the model layer.

Eight modules used to declare this protocol themselves, byte-identical in seven
cases. Structural typing makes that legal and it reads like good decoupling —
each module stating the minimum it needs — but it stopped being that the moment
the copies had to agree. Adding ``task``/``metadata`` in #208 and
``trace_id``/``session_id`` in #209 meant editing eight files twice in two days,
and each time the compiler was the only thing standing between a missed copy and
a call that silently would not typecheck at the one site nobody ran.

That is the same shape as the defect family the trading path kept producing: a
fact declared in more than one place, where the copies are free to disagree. The
fix is the same one — declare it once.

The protocol is deliberately **wider than any single caller needs**. It mirrors
:meth:`shrap.llm.client.TierLLMClient.complete` in full so that the real client
satisfies it without adaptation, and so a test fake written against it works
everywhere rather than only in the module it was written for.

``-> Any`` rather than ``-> LLMResult`` is carried over unchanged from the eight
originals. Tightening it is a real improvement and a separate one: every fake in
the test suite currently returns its own result class, and narrowing the return
type would rewrite all of them for a reason unrelated to this consolidation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class CompletionClient(Protocol):
    """Anything that can complete a prompt on a tier.

    Satisfied by :class:`shrap.llm.client.TierLLMClient` and by the fakes in
    ``tests/``. Nothing needs to subclass it — conformance is by shape.
    """

    async def complete(
        self,
        tier: str,
        prompt: str,
        system: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.2,
        think: bool | None = None,
        task: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
    ) -> Any: ...


__all__ = ["CompletionClient"]
