"""with_oauth_app.py gives the command the OAuth app in its environment, and not the admin login."""

from __future__ import annotations

import pytest

from sdk_tests import with_oauth_app

pytestmark = pytest.mark.unit


def test_the_command_gets_the_oauth_app_and_not_the_admin_login(monkeypatch):
    monkeypatch.setenv("PIPESHUB_BASE_URL", "http://app.test/")
    monkeypatch.setenv("PIPESHUB_TEST_USER_EMAIL", "admin@test")
    monkeypatch.setenv("PIPESHUB_TEST_USER_PASSWORD", "admin-password")
    monkeypatch.setattr(
        with_oauth_app, "obtain_local_oauth_credentials", lambda base_url: ("id", "secret")
    )
    calls = []
    monkeypatch.setattr(with_oauth_app.os, "execvpe", lambda *args: calls.append(args))

    with_oauth_app.main(["run_sdk_tests.sh", "current"])

    [(file, args, env)] = calls
    assert (file, args) == ("run_sdk_tests.sh", ["run_sdk_tests.sh", "current"])
    assert "PIPESHUB_TEST_USER_EMAIL" not in env
    assert "PIPESHUB_TEST_USER_PASSWORD" not in env
    oauth_keys = ("API_URL", "TOKEN_URL", "CLIENT_ID", "CLIENT_SECRET")
    assert {key: env[f"PIPESHUB_{key}"] for key in oauth_keys} == {
        "API_URL": "http://app.test/api/v1",
        "TOKEN_URL": "http://app.test/api/v1/oauth2/token",
        "CLIENT_ID": "id",
        "CLIENT_SECRET": "secret",
    }


def test_without_a_command_it_stops():
    with pytest.raises(SystemExit):
        with_oauth_app.main([])
