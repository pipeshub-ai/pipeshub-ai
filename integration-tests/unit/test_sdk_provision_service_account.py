"""The suites' OAuth apps act as the service account the provisioner seeded with."""

from __future__ import annotations

import pytest

from sdk_tests import provision_sdk_test_env

pytestmark = pytest.mark.unit


def test_the_service_account_reaches_later_steps(monkeypatch, tmp_path):
    github_env = tmp_path / "github_env"
    github_env.write_text("EARLIER=1\n")
    monkeypatch.setenv("GITHUB_ENV", str(github_env))
    monkeypatch.setenv(provision_sdk_test_env.SERVICE_ACCOUNT_ENV, "sa-123")

    provision_sdk_test_env.share_service_account_with_later_steps()

    assert github_env.read_text() == (
        f"EARLIER=1\n{provision_sdk_test_env.SERVICE_ACCOUNT_ENV}=sa-123\n"
    )


def test_outside_actions_nothing_is_written(monkeypatch):
    monkeypatch.delenv("GITHUB_ENV", raising=False)
    monkeypatch.setenv(provision_sdk_test_env.SERVICE_ACCOUNT_ENV, "sa-123")

    provision_sdk_test_env.share_service_account_with_later_steps()
