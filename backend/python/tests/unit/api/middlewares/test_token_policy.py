"""Unit tests for app.api.middlewares.token_policy."""

import pytest

from app.api.middlewares.token_policy import (
    ACCEPTED_SERVICE_SCOPES,
    AuthTokenType,
    has_service_scope,
    is_service_token,
    scope_value,
    token_scopes,
)
from app.config.constants.service import TokenScopes


def _service_user(**claims):
    return {"token_type": AuthTokenType.SCOPED.value, **claims}


class TestAcceptedServiceScopes:
    def test_allowlist_is_exactly_the_internal_service_flows(self):
        # Widening this set lets another token class authenticate against the
        # Python services, so any change must be deliberate.
        assert ACCEPTED_SERVICE_SCOPES == frozenset(
            {
                "connector:signedUrl",
                "record:content",
                "conversation:create",
                "fetch:config",
            }
        )

    @pytest.mark.parametrize(
        "user_held_or_node_only_scope",
        [
            TokenScopes.TOKEN_REFRESH,
            TokenScopes.PASSWORD_RESET,
            TokenScopes.SEND_MAIL,
            TokenScopes.STORAGE_TOKEN,
            TokenScopes.USER_LOOKUP,
        ],
    )
    def test_node_only_scopes_are_not_accepted(self, user_held_or_node_only_scope):
        assert user_held_or_node_only_scope.value not in ACCEPTED_SERVICE_SCOPES


class TestTokenScopes:
    def test_reads_scopes_list(self):
        assert token_scopes({"scopes": ["a", "b"]}) == frozenset({"a", "b"})

    def test_missing_claim_is_empty(self):
        assert token_scopes({}) == frozenset()

    def test_string_claim_is_not_split(self):
        assert token_scopes({"scopes": "fetch:config record:content"}) == frozenset()

    def test_non_string_members_are_dropped(self):
        assert token_scopes({"scopes": ["fetch:config", 1, None, {"x": 1}]}) == frozenset(
            {"fetch:config"}
        )

    def test_oauth_scope_claims_are_ignored(self):
        claims = {"scope": "fetch:config", "oauthScopes": ["record:content"]}
        assert token_scopes(claims) == frozenset()


class TestScopeValue:
    def test_enum_member(self):
        assert scope_value(TokenScopes.FETCH_CONFIG) == "fetch:config"

    def test_plain_string(self):
        assert scope_value("fetch:config") == "fetch:config"


class TestIsServiceToken:
    def test_scoped_token(self):
        assert is_service_token(_service_user()) is True

    def test_regular_token(self):
        assert is_service_token({"token_type": AuthTokenType.REGULAR.value}) is False

    def test_missing_token_type(self):
        assert is_service_token({}) is False

    def test_none(self):
        assert is_service_token(None) is False


class TestHasServiceScope:
    def test_matching_enum_scope(self):
        user = _service_user(scopes=["record:content"])
        assert has_service_scope(user, TokenScopes.RECORD_CONTENT) is True

    def test_matching_string_scope(self):
        user = _service_user(scopes=["record:content"])
        assert has_service_scope(user, "record:content") is True

    def test_any_of_several(self):
        user = _service_user(scopes=["fetch:config"])
        assert has_service_scope(
            user, TokenScopes.RECORD_CONTENT, TokenScopes.FETCH_CONFIG
        ) is True

    def test_non_matching_scope(self):
        user = _service_user(scopes=["fetch:config"])
        assert has_service_scope(user, TokenScopes.RECORD_CONTENT) is False

    def test_no_scopes_requested(self):
        assert has_service_scope(_service_user(scopes=["fetch:config"])) is False

    def test_regular_token_with_scopes_claim_is_not_a_service(self):
        user = {"token_type": AuthTokenType.REGULAR.value, "scopes": ["record:content"]}
        assert has_service_scope(user, TokenScopes.RECORD_CONTENT) is False

    def test_oauth_scopes_never_grant_service_scope(self):
        user = _service_user(oauthScopes=["connector:signedUrl"])
        assert has_service_scope(user, TokenScopes.CONNECTOR_SIGNED_URL) is False

    def test_none_user(self):
        assert has_service_scope(None, TokenScopes.RECORD_CONTENT) is False
