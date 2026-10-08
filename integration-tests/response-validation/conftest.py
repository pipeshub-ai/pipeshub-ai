"""Shared fixtures for the response-validation suite."""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Optional

import pytest

from helper.clients.config_client import ConfigClient

_HELPER = Path(__file__).parent / "helper"
if str(_HELPER) not in sys.path:
    sys.path.insert(0, str(_HELPER))

from strict_openapi import record_exchanges  # noqa: E402


def _gate(item: pytest.Item, phase: str) -> Iterator[None]:
    """A spec_audit test fails when any call it makes, in any phase, disagrees with the OpenAPI spec.

    STRICT_OPENAPI_GATE=1 holds every test under response-validation/ to the same rule.
    """
    if item.get_closest_marker("spec_audit") is None and os.getenv("STRICT_OPENAPI_GATE") != "1":
        yield
        return
    with record_exchanges() as problems:
        outcome = yield
    if problems and outcome.excinfo is None:
        listing = "\n".join(problems)
        outcome.force_exception(
            AssertionError(f"{len(problems)} OpenAPI problem(s) in calls made during {phase}:\n{listing}")
        )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Iterator[None]:
    yield from _gate(item, "setup")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Iterator[None]:
    yield from _gate(item, "the test")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Iterator[None]:
    yield from _gate(item, "cleanup")


def _smtp_env() -> Optional[dict]:
    """Build an SMTP config from env, or None if not configured.

    CI provides SMTP_HOST/PORT/USERNAME/PASSWORD as secrets. Locally, point
    these at Mailpit (SMTP_HOST=mailpit SMTP_PORT=1025, no credentials).
    """
    host = os.getenv("SMTP_HOST")
    port = os.getenv("SMTP_PORT")
    if not host or not port:
        return None
    username = os.getenv("SMTP_USERNAME", "")
    return {
        "host": host,
        "port": int(port),
        "username": username,
        "password": os.getenv("SMTP_PASSWORD", ""),
        "fromEmail": os.getenv("SMTP_FROM_EMAIL") or username or "no-reply@example.com",
    }


@pytest.fixture(scope="session")
def smtp_configured(config_client: ConfigClient) -> None:
    """Configure backend SMTP from the SMTP_* env for SMTP-gated routes.

    Any response-validation test that hits a route behind smtpConfigCheck
    (bulk invite, OTP login, forgot-password) can depend on this. Skips the
    dependent test(s) when SMTP_HOST/SMTP_PORT are unset, so local runs without
    a mail server stay green.
    """
    smtp = _smtp_env()
    if smtp is None:
        pytest.skip("SMTP_HOST/SMTP_PORT not set — skipping SMTP-dependent test")
    resp = config_client.create_smtp_config(**smtp)
    assert resp.status_code in (200, 201), (
        f"Failed to configure SMTP: {resp.status_code} {resp.text}"
    )


@pytest.fixture(scope="session", autouse=True)
def _spec_audit_stack_prepared(request: pytest.FixtureRequest) -> None:
    """Seed AI models and SMTP before the first spec_audit test.

    A stack the audit meets first has neither. The models go in before anything
    is indexed: PipesHub refuses to change the embedding model once vectors are
    stored.
    """
    if not any(item.get_closest_marker("spec_audit") for item in request.session.items):
        return
    request.getfixturevalue("ai_models_configured")
    if _smtp_env() is not None:
        request.getfixturevalue("smtp_configured")
