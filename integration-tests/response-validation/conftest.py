"""Shared fixtures for the response-validation suite."""

from __future__ import annotations

import pytest

from helper.clients.config_client import ConfigClient
from helper.smtp_env import smtp_env


@pytest.fixture(scope="session")
def smtp_configured(config_client: ConfigClient) -> None:
    """Configure backend SMTP from the SMTP_* env for SMTP-gated routes.

    Any response-validation test that hits a route behind smtpConfigCheck
    (bulk invite, OTP login, forgot-password) can depend on this. Skips the
    dependent test(s) when SMTP_HOST/SMTP_PORT are unset, so local runs without
    a mail server stay green.
    """
    smtp = smtp_env()
    if smtp is None:
        pytest.skip("SMTP_HOST/SMTP_PORT not set — skipping SMTP-dependent test")
    resp = config_client.create_smtp_config(**smtp)
    assert resp.status_code in (200, 201), (
        f"Failed to configure SMTP: {resp.status_code} {resp.text}"
    )
