"""The Python service-token scopes mirror the Node.js TokenScopes."""

from app.config.constants.service import TokenScopes


def test_scope_values_are_unique() -> None:
    values = [scope.value for scope in TokenScopes]
    assert len(values) == len(set(values))
