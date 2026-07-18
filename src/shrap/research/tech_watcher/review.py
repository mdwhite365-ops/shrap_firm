"""Review page CLI: `shrap-tech-watcher-review`.

Renders the candidate list as markdown for Mike's gatekeeping — the sprint's
"static review page." Proposed candidates first with their full evidence
trail, then the rejection graveyard (always shown: the survivorship-bias
rule says the denominator is never hidden). Promotion remains Mike's
explicit action and is a later card; this surface is read-only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

from shrap.common.db import create_asyncpg_pool
from shrap.research.tech_watcher.candidates import PostgresCandidateStore
from shrap.research.tech_watcher.synthesis import STATUS_PROPOSED, STATUS_REJECTED


def _loaded(value: object) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def render_markdown(candidates: list[dict[str, Any]]) -> str:
    proposed = [c for c in candidates if c["status"] == STATUS_PROPOSED]
    rejected = [c for c in candidates if c["status"] == STATUS_REJECTED]
    lines = [
        "# Tech Watcher — world-changer candidates",
        "",
        f"Proposed: {len(proposed)} · Rejected (graveyard): {len(rejected)}",
        "",
        "Promotion is Mike's call; nothing on this page is auto-promoted.",
        "",
        "## Proposed",
        "",
    ]
    if not proposed:
        lines.append("(none yet)")
    for c in proposed:
        kill_criteria = _loaded(c.get("kill_criteria")) or []
        sources = _loaded(c.get("source_classes")) or []
        lines += [
            f"### {c['name']}  (`{c['candidate_id']}`)",
            "",
            f"- **Archetype:** {c['archetype']}",
            f"- **Confidence:** {c['confidence']} · **Horizon:** {c['expected_impact_horizon']}",
            f"- **Source classes:** {', '.join(sources)}",
            f"- **Falsifier horizon:** {c.get('falsifier_horizon') or 'n/a'}",
            "",
            str(c["thesis"]),
            "",
            "**Kill criteria:**",
            *[f"- {k}" for k in kill_criteria],
            "",
        ]
    lines += ["## Rejection graveyard", ""]
    if not rejected:
        lines.append("(empty)")
    for c in rejected:
        lines.append(
            f"- `{c['candidate_id']}` [{c['archetype']}] {c['name']} — "
            f"{c.get('rejection_reason') or 'unknown reason'}"
        )
    lines.append("")
    return "\n".join(lines)


async def _render(postgres_dsn: str) -> str:
    pool = await create_asyncpg_pool(postgres_dsn)
    try:
        store = PostgresCandidateStore(pool)
        candidates = await store.candidates_by_status([STATUS_PROPOSED, STATUS_REJECTED])
    finally:
        await pool.close()
    return render_markdown(candidates)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the Tech Watcher review page.")
    parser.add_argument(
        "--dsn",
        default=os.environ.get(
            "TECH_WATCHER_POSTGRES_DSN", "postgresql://shrap:shrap@postgres:5432/shrap"
        ),
        help="Postgres DSN (default: TECH_WATCHER_POSTGRES_DSN env)",
    )
    parser.add_argument("--out", default=None, help="Write markdown here instead of stdout")
    args = parser.parse_args()
    markdown = asyncio.run(_render(args.dsn))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(markdown)
    else:
        print(markdown)


if __name__ == "__main__":
    main()
