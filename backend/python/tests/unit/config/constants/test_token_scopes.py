"""The Python service-token scopes mirror the Node.js TokenScopes."""

from app.config.constants.service import TokenScopes


def test_authz_check_mirrors_node() -> None:
    assert TokenScopes.AUTHZ_CHECK.value == "authz:check"


def test_scope_values_are_unique() -> None:
    values = [scope.value for scope in TokenScopes]
    assert len(values) == len(set(values))
