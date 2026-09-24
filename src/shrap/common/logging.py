"""structlog JSON logging configuration."""

from __future__ import annotations

import logging
import re
import sys

import structlog

# **Secrets travel in some URLs, and httpx logs every URL at INFO.** A Discord
# webhook's token is a path segment, so the Health Monitor printed the full
# webhook — enough to post to the channel — on every alert it sent, into
# `docker logs` (found 2026-09-23). The request lines themselves are worth
# keeping: they are what diagnosed the arXiv 406s the same day. So they are
# redacted rather than silenced.
_REDACTED = "***"
_URL_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Discord (and Slack-style) webhooks: /api/webhooks/<id>/<token>
    (re.compile(r"(/api/webhooks/\d+/)[^/?#\s\"]+"), rf"\g<1>{_REDACTED}"),
    # Credential-named query parameters.
    (
        re.compile(
            r"([?&](?:api[_-]?key|apikey|key|token|access_token|secret|password|sig|signature)=)"
            r"[^&#\s\"]+",
            re.IGNORECASE,
        ),
        rf"\g<1>{_REDACTED}",
    ),
    # user:password@ in the authority.
    (re.compile(r"(https?://)[^/@\s\"]+@"), rf"\g<1>{_REDACTED}@"),
)


def redact_url_secrets(text: str) -> str:
    """Mask the parts of any URL in ``text`` that are credentials."""

    for pattern, replacement in _URL_SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class _RedactUrlSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_url_secrets(record.getMessage())
        record.args = None
        return True


def install_url_redaction() -> None:
    """Redact credentials from the URLs httpx logs. Idempotent."""

    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _RedactUrlSecrets) for f in logger.filters):
            logger.addFilter(_RedactUrlSecrets())


def configure_logging(service: str, level: str = "INFO") -> None:
    """Configure structlog to emit JSON lines with a 'service' field auto-attached."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )
    install_url_redaction()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    structlog.contextvars.bind_contextvars(service=service)
