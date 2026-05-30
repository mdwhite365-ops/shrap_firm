"""Alert dispatch (ADR-0005).

Discord webhook for routine alerts, ntfy.sh for urgent (system-wide) alerts.
Delivery failures are logged and surfaced as ops.alert-delivery-failed events
but MUST NOT raise — the monitor's health is more important than any single
alert delivery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import structlog
from pydantic import SecretStr

if TYPE_CHECKING:
    from shrap.agents.operations.health_monitor.checks import CheckResult
    from shrap.agents.operations.health_monitor.config import Settings
    from shrap.common.redis_client import RedisStreamClient

log = structlog.get_logger(__name__)

_DISCORD_COLORS = {
    "info": 0x3498DB,
    "warn": 0xF1C40F,
    "error": 0xE74C3C,
    "recovered": 0x2ECC71,
}


async def send_discord(
    client: httpx.AsyncClient,
    webhook_url: SecretStr,
    title: str,
    body: str,
    severity: str = "warn",
) -> None:
    """POST to a Discord webhook. Raises httpx.HTTPError on failure."""
    color = _DISCORD_COLORS.get(severity, _DISCORD_COLORS["warn"])
    payload = {
        "embeds": [
            {
                "title": title,
                "description": body,
                "color": color,
            }
        ]
    }
    resp = await client.post(
        webhook_url.get_secret_value(), json=payload, timeout=10.0
    )
    resp.raise_for_status()


async def send_ntfy(
    client: httpx.AsyncClient,
    ntfy_url: str,
    title: str,
    body: str,
    priority: int = 4,
) -> None:
    """POST plain-text body to an ntfy.sh topic URL."""
    headers = {
        "Title": title,
        "Priority": str(priority),
        "Tags": "rotating_light",
    }
    resp = await client.post(
        ntfy_url, content=body.encode("utf-8"), headers=headers, timeout=10.0
    )
    resp.raise_for_status()


async def _publish_failure(
    redis: RedisStreamClient | None,
    settings: Settings,
    channel: str,
    reason: str,
    check_name: str,
) -> None:
    if redis is None or settings.dry_run:
        return
    # Import locally to avoid cycle at module load.
    from shrap.common.envelope import Envelope

    try:
        env = Envelope.new(
            produced_by=settings.produced_by(),
            schema_version="1.0.0",
            payload={
                "channel": channel,
                "reason": reason,
                "check": check_name,
            },
        )
        await redis.xadd("ops.alert-delivery-failed", env)
    except Exception:
        log.exception("alert.failure_publish_failed", check=check_name)


async def dispatch(
    transition: str,
    check: CheckResult,
    settings: Settings,
    *,
    http_client: httpx.AsyncClient,
    redis: RedisStreamClient | None = None,
    system_wide: bool = False,
) -> None:
    """Route an alert. NEVER raises.

    - degraded-confirmed → Discord (routine).
    - degraded-confirmed AND system_wide (>=2 checks degraded) → ntfy.sh urgent.
    - recovered-confirmed → Discord (routine, recovered color).
    """
    if settings.dry_run:
        log.info(
            "alert.dispatch.dry_run",
            transition=transition,
            check=check.name,
            status=check.status,
            system_wide=system_wide,
        )
        return

    title_prefix = "[shrap]"
    title: str
    severity: str
    body = (
        f"check={check.name} status={check.status} "
        f"latency_ms={check.latency_ms:.0f} evidence={check.evidence}"
    )

    if transition == "recovered-confirmed":
        title = f"{title_prefix} RECOVERED — {check.name}"
        severity = "recovered"
    else:
        title = f"{title_prefix} DEGRADED — {check.name}"
        severity = "error" if check.status == "down" else "warn"

    sent_any = False

    if settings.discord_webhook_url is not None:
        try:
            await send_discord(
                http_client, settings.discord_webhook_url, title, body, severity
            )
            sent_any = True
        except Exception as e:
            log.exception("alert.discord_failed", check=check.name, error=str(e))
            await _publish_failure(redis, settings, "discord", str(e), check.name)

    if system_wide and settings.ntfy_url:
        try:
            await send_ntfy(http_client, settings.ntfy_url, title, body, priority=5)
            sent_any = True
        except Exception as e:
            log.exception("alert.ntfy_failed", check=check.name, error=str(e))
            await _publish_failure(redis, settings, "ntfy", str(e), check.name)

    if not sent_any:
        log.warning(
            "alert.no_channel_configured",
            transition=transition,
            check=check.name,
        )


__all__ = ["dispatch", "send_discord", "send_ntfy"]
