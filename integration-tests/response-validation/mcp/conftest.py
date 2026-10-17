"""
Fixtures for the MCP server surface tests.

One OAuth app with every scope and every grant type is created for the
session and deleted at the end. One MCP session reads the surface once;
every test asserts against that dict.

The expected surface lives in ``golden/mcp_surface_<version>.json`` where
``<version>`` is the ``@pipeshub-ai/mcp`` pin in
``backend/nodejs/apps/package.json``. Bumping the pin therefore needs a new
golden, generated with ``pytest response-validation/mcp --update-mcp-golden``.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Iterator

import pytest

from helper.local_auth import obtain_user_session_token
from helper.mcp_client import read_mcp_surface
from helper.mcp_oauth import (
    OAuthApp,
    authorization_code_token,
    client_credentials_token,
    create_oauth_app,
    delete_oauth_app,
)
from helper.mcp_pin import mcp_package_pin

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parents[2]
GOLDEN_DIR = _THIS_DIR / "golden"


@pytest.fixture(scope="session")
def mcp_base_url() -> str:
    base_url = os.getenv("PIPESHUB_BASE_URL", "").rstrip("/")
    if not base_url:
        pytest.skip("PIPESHUB_BASE_URL is not set")
    return base_url


@pytest.fixture(scope="session")
def mcp_user_jwt(mcp_base_url: str) -> str:
    if not (os.getenv("PIPESHUB_TEST_USER_EMAIL") and os.getenv("PIPESHUB_TEST_USER_PASSWORD")):
        pytest.skip("PIPESHUB_TEST_USER_EMAIL / PIPESHUB_TEST_USER_PASSWORD are not set")
    return obtain_user_session_token(mcp_base_url)


@pytest.fixture(scope="session")
def mcp_oauth_app(mcp_base_url: str, mcp_user_jwt: str) -> Iterator[OAuthApp]:
    """Full-access OAuth app, as a host would register. Deleted after the session."""
    app = create_oauth_app(
        mcp_base_url,
        mcp_user_jwt,
        name=f"integration-mcp-surface-{uuid.uuid4().hex[:12]}",
    )
    try:
        yield app
    finally:
        try:
            delete_oauth_app(mcp_base_url, mcp_user_jwt, app.id)
        except Exception:  # noqa: BLE001 - teardown must not mask test results
            pass


@pytest.fixture(scope="session")
def mcp_access_token(mcp_base_url: str, mcp_user_jwt: str, mcp_oauth_app: OAuthApp) -> str:
    """Authorization-code + PKCE token: the token a real MCP host holds."""
    return authorization_code_token(mcp_base_url, mcp_user_jwt, mcp_oauth_app)["access_token"]


@pytest.fixture(scope="session")
def mcp_client_credentials_token(mcp_base_url: str, mcp_oauth_app: OAuthApp) -> str:
    return client_credentials_token(mcp_base_url, mcp_oauth_app)["access_token"]


@pytest.fixture(scope="session")
def mcp_surface(mcp_base_url: str, mcp_access_token: str) -> dict:
    return read_mcp_surface(mcp_base_url, mcp_access_token)


@pytest.fixture(scope="session")
def mcp_golden_path() -> Path:
    return GOLDEN_DIR / f"mcp_surface_{mcp_package_pin()}.json"


@pytest.fixture(scope="session")
def mcp_golden(request: pytest.FixtureRequest, mcp_golden_path: Path, mcp_surface: dict) -> dict:
    """
    The expected surface. With ``--update-mcp-golden`` the live surface is
    written first, so a run that regenerates the golden also passes.
    """
    if request.config.getoption("--update-mcp-golden"):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        mcp_golden_path.write_text(
            json.dumps(mcp_surface, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        )
    if not mcp_golden_path.exists():
        pytest.fail(
            f"No golden surface for @pipeshub-ai/mcp {mcp_package_pin()} at "
            f"{mcp_golden_path.relative_to(_REPO_ROOT)}. Review the served text, then run: "
            "pytest response-validation/mcp --update-mcp-golden"
        )
    return json.loads(mcp_golden_path.read_text())
