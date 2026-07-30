"""What a module drags in when you import it, which is a deployment fact.

Agent images install only what their agent needs. `tech-watcher.Dockerfile` has
no numpy; `strategy-evaluator.Dockerfile` does. That boundary is invisible in
the source and absolute at runtime, and nothing in a local test run or a
`mypy --strict` pass can see it — the dev environment has everything.

It bit on 2026-07-30. PR #157 gave Tech Watcher a line reading

    from shrap.research.hypothesis_generator.literature import PostgresLiteratureStore

against a module whose own imports are stdlib only. But the package's
``__init__`` re-exported ``HypothesisGenerator`` for convenience, and a
submodule import runs the package ``__init__`` first — so that one line pulled
in the strategy evaluator, then numpy, and the service crash-looped on
``ModuleNotFoundError`` behind a traceback that named the innocent module.

368 modules before the fix, 39 after.

These tests measure the real thing rather than reading imports, because the cost
is transitive: a module can be stdlib-clean and still be expensive through four
hops. Each runs in a subprocess, since import side effects do not unwind and a
sibling test that already loaded numpy would make this pass wrongly.
"""

from __future__ import annotations

import subprocess
import sys

# Third-party packages absent from at least one agent image. Importing one of
# these from a module an image-lacking service uses is a crash loop, not a slow
# import.
_NOT_EVERYWHERE = ("numpy", "pandas")

_PROBE = """
import sys
before = set(sys.modules)
import {module}
new = set(sys.modules) - before
roots = {{m.split(".")[0] for m in new}}
heavy = sorted(roots & {{{forbidden}}})
internal = sorted(m for m in new if m.startswith("shrap.") and m.count(".") <= 2)
print("HEAVY:" + ",".join(heavy))
print("COUNT:" + str(len(new)))
print("SHRAP:" + ",".join(sorted(set(internal))))
"""


def _probe(module: str) -> dict[str, str]:
    forbidden = ", ".join(repr(p) for p in _NOT_EVERYWHERE)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE.format(module=module, forbidden=forbidden)],
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(line.split(":", 1) for line in result.stdout.strip().splitlines() if ":" in line)


def test_the_literature_contract_is_importable_without_numpy() -> None:
    """The exact line Tech Watcher runs. If this fails, the tech-watcher
    container will not start — and the traceback will name this module rather
    than whatever re-export actually pulled numpy in."""

    assert _probe("shrap.research.hypothesis_generator.literature")["HEAVY"] == ""


def test_the_generator_package_root_re_exports_nothing() -> None:
    """A convenience re-export in an ``__init__`` is an invisible dependency
    edge from every consumer of every submodule to every module the package
    touches. Importing the package alone must stay cheap."""

    assert _probe("shrap.research.hypothesis_generator")["HEAVY"] == ""


def test_the_tech_watcher_literature_filter_stays_light() -> None:
    """It runs inside the tech-watcher image, which has redis, httpx,
    structlog, pydantic and asyncpg — and nothing numerical."""

    probe = _probe("shrap.research.tech_watcher.literature_filter")

    assert probe["HEAVY"] == ""
    assert "shrap.research.strategy_evaluator" not in probe["SHRAP"]


def test_the_evaluator_is_allowed_to_be_heavy() -> None:
    """The other half of the boundary, asserted so the rule reads as a boundary
    rather than a blanket ban. `strategy-evaluator.Dockerfile` installs numpy
    and pandas precisely because the stats core needs them."""

    assert "numpy" in _probe("shrap.research.strategy_evaluator.engine")["HEAVY"]
