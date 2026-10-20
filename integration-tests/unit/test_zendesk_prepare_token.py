"""Token minting checks that do not contact Zendesk."""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from connectors.zendesk import prepare_token


def _response(status: int, payload: object) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status, is_success=200 <= status < 300, json=lambda: payload
    )


def test_mint_uses_the_client_credentials_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_post(url: str, **kwargs: object) -> SimpleNamespace:
        calls.append((url, kwargs))
        return _response(200, {"access_token": "new-access", "token_type": "bearer"})

    monkeypatch.setattr(prepare_token.httpx, "post", fake_post)
    assert prepare_token.mint_token("inai-90216", "client", "secret") == "new-access"
    assert calls[0][0] == "https://inai-90216.zendesk.com/oauth/tokens"
    assert calls[0][1]["json"] == {
        "grant_type": "client_credentials",
        "client_id": "client",
        "client_secret": "secret",
        "scope": "read",
        "expires_in": 21600,
    }


def test_mint_reports_the_oauth_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prepare_token.httpx,
        "post",
        lambda *_args, **_kwargs: _response(400, {"error": "unauthorized_client"}),
    )
    with pytest.raises(RuntimeError, match=r"HTTP 400 \(unauthorized_client\)"):
        prepare_token.mint_token("inai-90216", "client", "secret")


def test_mint_requires_an_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        prepare_token.httpx, "post", lambda *_args, **_kwargs: _response(200, {})
    )
    with pytest.raises(RuntimeError, match="no access token"):
        prepare_token.mint_token("inai-90216", "client", "secret")


def test_mint_refuses_a_host_as_subdomain() -> None:
    with pytest.raises(ValueError, match="Invalid Zendesk subdomain"):
        prepare_token.mint_token("evil.example/x", "client", "secret")


def test_main_writes_an_owner_only_token_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "zendesk-access-token"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("ZENDESK_TEST_SUBDOMAIN", "inai-90216")
    monkeypatch.setenv("ZENDESK_TEST_CLIENT_ID", "client")
    monkeypatch.setenv("ZENDESK_TEST_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ZENDESK_TEST_ACCESS_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(prepare_token, "mint_token", lambda *_args: "new-access")
    assert prepare_token.main() == 0
    assert token_file.read_text() == "new-access"
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
