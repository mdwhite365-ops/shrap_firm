"""The shared `CompletionClient` must keep describing the real client.

Eight modules used to declare this protocol themselves. Consolidating them into
one removed the risk that the copies disagree and introduced a smaller one in
its place: a single declaration that drifts from
:class:`shrap.llm.client.TierLLMClient` now misdescribes *every* agent at once.

mypy catches the important half already — every agent takes a
``CompletionClient`` and every service hands it a ``TierLLMClient``, so a client
that stopped satisfying the protocol fails the typecheck. What mypy does not
catch is the reverse drift: a parameter added to the client and forgotten on the
protocol typechecks fine everywhere and simply leaves the new capability
unreachable through the interface every caller actually uses. That is what this
pins.
"""

from __future__ import annotations

import inspect

from shrap.llm import CompletionClient, TierLLMClient


def _params(func: object) -> dict[str, inspect.Parameter]:
    return dict(inspect.signature(func).parameters)  # type: ignore[arg-type]


def test_protocol_and_client_take_the_same_parameters() -> None:
    protocol = _params(CompletionClient.complete)
    client = _params(TierLLMClient.complete)

    assert set(protocol) == set(client), (
        "shrap.llm.protocol.CompletionClient has drifted from TierLLMClient.complete. "
        "Every agent depends on the protocol, so a parameter that exists only on the "
        "client is unreachable through the interface callers use."
    )


def test_protocol_defaults_match_the_client() -> None:
    protocol = _params(CompletionClient.complete)
    client = _params(TierLLMClient.complete)

    for name, expected in client.items():
        if name == "self":
            continue
        assert protocol[name].default == expected.default, (
            f"default for {name!r} differs between the protocol and the client"
        )
