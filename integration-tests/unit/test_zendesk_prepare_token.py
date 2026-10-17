"""Token rotation checks that do not contact Zendesk or GitHub."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from connectors.zendesk import prepare_token


def test_exchange_requests_fresh_access_and_rotated_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_post(url: str, **kwargs: object) -> SimpleNamespace:
        calls.append((url, kwargs))
        return SimpleNamespace(
            is_success=True,
            json=lambda: {"access_token": "new-access", "refresh_token": "new-refresh"},
        )

    monkeypatch.setattr(prepare_token.httpx, "post", fake_post)
    assert prepare_token.exchange_token(
        "inai-90216", "client", "secret", "old-refresh"
    ) == (
        "new-access",
        "new-refresh",
    )
    assert calls[0][0] == "https://inai-90216.zendesk.com/oauth/tokens"
    assert calls[0][1]["json"] == {
        "grant_type": "refresh_token",
        "client_id": "client",
        "client_secret": "secret",
        "refresh_token": "old-refresh",
        "expires_in": 86400,
    }


def test_exchange_requires_a_new_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prepare_token.httpx,
        "post",
        lambda *_args, **_kwargs: SimpleNamespace(
            is_success=True,
            json=lambda: {"access_token": "new-access"},
        ),
    )
    with pytest.raises(RuntimeError, match="both rotated tokens"):
        prepare_token.exchange_token("inai-90216", "client", "secret", "old-refresh")


def test_ci_saves_rotated_token_to_protected_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("ZENDESK_TEST_SECRET_ENVIRONMENT", "integration-test-dev")
    monkeypatch.setenv("GH_TOKEN", "writer")
    calls = []

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(prepare_token.subprocess, "run", fake_run)
    prepare_token.persist_refresh_token("new-refresh")
    assert calls[0][0] == [
        "gh",
        "secret",
        "set",
        "ZENDESK_TEST_REFRESH_TOKEN",
        "--env",
        "integration-test-dev",
        "--repo",
        "owner/repo",
    ]
    assert calls[0][1]["input"] == "new-refresh"
    assert calls[0][1]["capture_output"] is True
