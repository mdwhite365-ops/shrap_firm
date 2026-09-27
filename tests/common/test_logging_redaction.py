"""httpx logs every request URL at INFO, and some URLs carry credentials."""

from __future__ import annotations

import logging

import pytest

from shrap.common import logging as shrap_logging
from shrap.common.logging import install_url_redaction, redact_url_secrets

WEBHOOK = "https://discord.com/api/webhooks/1454563493624811582/abcDEF-ghi_123"


def test_a_discord_webhook_token_is_masked_and_its_id_kept() -> None:
    assert (
        redact_url_secrets(f'HTTP Request: POST {WEBHOOK} "HTTP/1.1 204 No Content"')
        == "HTTP Request: POST https://discord.com/api/webhooks/1454563493624811582/*** "
        '"HTTP/1.1 204 No Content"'
    )


@pytest.mark.parametrize("param", ["api_key", "apikey", "token", "key", "access_token"])
def test_credential_query_parameters_are_masked(param: str) -> None:
    url = f"https://example.com/x?feed=iex&{param}=s3cr3t&page=2"
    assert redact_url_secrets(url) == f"https://example.com/x?feed=iex&{param}=***&page=2"


def test_userinfo_is_masked() -> None:
    assert redact_url_secrets("https://user:pw@host/p") == "https://***@host/p"


def test_an_ordinary_request_line_is_left_alone() -> None:
    line = (
        "HTTP Request: GET https://export.arxiv.org/api/query?search_query=cat%3Aq-fin.PM"
        '&max_results=100 "HTTP/1.1 406 Not Acceptable"'
    )
    assert redact_url_secrets(line) == line


def test_configure_logging_installs_the_redaction() -> None:
    """Asserted by source rather than by calling it: configure_logging rewrites
    structlog's global config, which breaks later tests that capture logs."""

    import inspect

    assert "install_url_redaction()" in inspect.getsource(shrap_logging.configure_logging)


def test_the_httpx_logger_redacts_once_installed(caplog: pytest.LogCaptureFixture) -> None:
    """The wiring, not just the function: the line httpx actually emits."""

    install_url_redaction()
    install_url_redaction()  # idempotent
    assert len(logging.getLogger("httpx").filters) == 1
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info(
            'HTTP Request: %s %s "%s %d %s"', "POST", WEBHOOK, "HTTP/1.1", 204, "No Content"
        )

    assert "abcDEF-ghi_123" not in caplog.text
    assert "/api/webhooks/1454563493624811582/***" in caplog.text
