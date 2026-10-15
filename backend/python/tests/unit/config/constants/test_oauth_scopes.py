"""The Python scope enum mirrors the Node.js OAuthScopeNames for the conversation scopes."""

from app.config.constants.service import OAuthScopes


def test_conversation_scopes_mirror_node() -> None:
    assert OAuthScopes.CONVERSATION_READ.value == "conversation:read"
    assert OAuthScopes.CONVERSATION_WRITE.value == "conversation:write"
    assert OAuthScopes.CONVERSATION_CHAT.value == "conversation:chat"


def test_scope_values_are_unique() -> None:
    values = [scope.value for scope in OAuthScopes]
    assert len(values) == len(set(values))
