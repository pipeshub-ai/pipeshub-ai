"""The integration test client must be granted every scope an OAuth app can hold.

A feature that adds a scope (Projects added project:*) otherwise leaves every
test of that feature failing with "403 Insufficient scope" in the nightly run.
This fails first, in the helper unit tests, naming the scope to add.

The list is read from ``OAuthScopes``, the registry that creating an OAuth app
validates against. The Node tests keep every scope a route requires in it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from helper.local_auth import TEST_CLIENT_SCOPES

pytestmark = pytest.mark.unit

SCOPES_REGISTRY = (
    Path(__file__).resolve().parents[2]
    / "backend/nodejs/apps/src/modules/oauth_provider/config/scopes.config.ts"
)


def _backend_scopes() -> set[str]:
    source = SCOPES_REGISTRY.read_text()
    block = source[source.index("export const OAuthScopes") :]
    block = block[: block.index("\n};")]
    return set(re.findall(r"^\s*name:\s*'([^']+)'", block, re.MULTILINE))


def test_the_test_client_is_granted_every_backend_scope() -> None:
    backend = _backend_scopes()
    assert backend, f"No scopes parsed from {SCOPES_REGISTRY}"
    missing = sorted(backend - set(TEST_CLIENT_SCOPES))
    assert not missing, (
        f"Add {missing} to TEST_CLIENT_SCOPES in helper/local_auth.py; tests that "
        "use these scopes get 403 without them."
    )


def test_the_test_client_asks_for_no_scope_the_backend_lacks() -> None:
    unknown = sorted(set(TEST_CLIENT_SCOPES) - _backend_scopes())
    assert not unknown, f"TEST_CLIENT_SCOPES lists scopes the backend does not define: {unknown}"
