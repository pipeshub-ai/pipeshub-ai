import pytest
from starlette.requests import Request

from app.config.redaction import reveal_requested


def _request(query: str, user: dict | None = None) -> Request:
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                       "query_string": query.encode()})
    request.state.user = user if user is not None else {"userId": "u1"}
    return request


def test_reveal_true_is_a_reveal() -> None:
    assert reveal_requested(_request("reveal=true")) is True


@pytest.mark.parametrize("query", [
    "", "reveal=false", "reveal=TRUE", "reveal=1", "reveal=yes", "reveal=",
    "reveal=false&reveal=true", "reveal=true&reveal=true", "reveal[]=true",
])
def test_anything_else_is_not_a_reveal(query: str) -> None:
    assert reveal_requested(_request(query)) is False


def test_oauth_app_tokens_cannot_reveal() -> None:
    assert reveal_requested(_request("reveal=true", {"userId": "u1", "isOAuth": True})) is False


# --- HIDE_SECRET_CONFIG on the OSS credential reads -------------------------

# The routes module only loads cleanly once app.edition_config is imported first.
import app.edition_config  # noqa: E402,F401
from app.api.routes.toolset_resolvers import mask_oauth_secrets  # noqa: E402
from app.api.routes.toolsets import _instance_for_response  # noqa: E402
from app.config.redaction import REDACTED_PLACEHOLDER  # noqa: E402
from app.connectors.api.connector_resolvers import (  # noqa: E402
    mask_oauth_config_for_response,
    strip_redacted_fields,
)

OAUTH_APP = {"orgId": "o1", "config": {"clientId": "cid", "clientSecret": "s3cret", "domain": "acme"}}


@pytest.fixture
def hidden(monkeypatch):
    monkeypatch.setenv("HIDE_SECRET_CONFIG", "true")


@pytest.fixture
def shown(monkeypatch):
    # "false" shows in both editions; EE treats an unset flag as hidden.
    monkeypatch.setenv("HIDE_SECRET_CONFIG", "false")
    monkeypatch.delenv("PLATFORM_POLICY_CREATION", raising=False)
    monkeypatch.delenv("PLATFORM_POLICY_SIGNUP", raising=False)


class TestConnectorOAuthApp:
    def test_hidden_masks_only_the_secret(self, hidden) -> None:
        out = mask_oauth_config_for_response(OAUTH_APP, "o1", is_admin=True)
        assert out["config"] == {"clientId": "cid", "clientSecret": REDACTED_PLACEHOLDER, "domain": "acme"}

    def test_hidden_reveal_returns_the_stored_secret(self, hidden) -> None:
        out = mask_oauth_config_for_response(OAUTH_APP, "o1", is_admin=True, reveal=True)
        assert out["config"]["clientSecret"] == "s3cret"

    def test_flag_off_returns_the_stored_secret(self, shown) -> None:
        assert mask_oauth_config_for_response(OAUTH_APP, "o1", is_admin=True)["config"]["clientSecret"] == "s3cret"

    def test_members_get_no_config_even_when_revealing(self, shown) -> None:
        assert mask_oauth_config_for_response(OAUTH_APP, "o1", is_admin=False, reveal=True)["config"] == {}

    def test_masking_does_not_touch_the_stored_document(self, hidden) -> None:
        mask_oauth_config_for_response(OAUTH_APP, "o1", is_admin=True)
        assert OAUTH_APP["config"]["clientSecret"] == "s3cret"

    def test_a_save_drops_the_echoed_mask_and_keeps_real_values(self) -> None:
        cleaned = strip_redacted_fields({"clientId": "new-id", "clientSecret": REDACTED_PLACEHOLDER})
        assert cleaned == {"clientId": "new-id"}


class TestActionOAuthApp:
    def test_hidden_masks_the_secret(self, hidden) -> None:
        assert mask_oauth_secrets({"clientId": "cid", "clientSecret": "s"})["clientSecret"] == REDACTED_PLACEHOLDER

    def test_reveal_and_flag_off_return_it(self, hidden) -> None:
        assert mask_oauth_secrets({"clientSecret": "s"}, reveal=True) == {"clientSecret": "s"}

    def test_flag_off_returns_it(self, shown) -> None:
        assert mask_oauth_secrets({"clientSecret": "s"}) == {"clientSecret": "s"}


class TestActionInlineCredentials:
    INSTANCE = {"_id": "i1", "auth": {"baseUrl": "https://jira", "apiToken": "tok", "tokens": {"access": "x"}}}
    SECRET_FIELDS = frozenset({"apiToken"})

    def test_hidden_masks_declared_secrets_and_stored_tokens(self, hidden) -> None:
        auth = _instance_for_response(self.INSTANCE, is_admin=True, secret_fields=self.SECRET_FIELDS)["auth"]
        assert auth == {"baseUrl": "https://jira", "apiToken": REDACTED_PLACEHOLDER, "tokens": REDACTED_PLACEHOLDER}

    def test_flag_off_shows_the_api_token_but_never_stored_tokens(self, shown) -> None:
        auth = _instance_for_response(self.INSTANCE, is_admin=True, secret_fields=self.SECRET_FIELDS)["auth"]
        assert auth["apiToken"] == "tok" and auth["tokens"] == REDACTED_PLACEHOLDER

    def test_reveal_returns_the_stored_auth(self, hidden) -> None:
        auth = _instance_for_response(
            self.INSTANCE, is_admin=True, reveal=True, secret_fields=self.SECRET_FIELDS
        )["auth"]
        assert auth["apiToken"] == "tok"

    def test_members_never_get_auth(self, shown) -> None:
        assert "auth" not in _instance_for_response(self.INSTANCE, is_admin=False, reveal=True)
