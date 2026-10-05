"""Shared fixtures for the strict OpenAPI audit of /api/v1/document."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_INTEGRATION_ROOT = Path(__file__).resolve().parents[3]
for _p in (_INTEGRATION_ROOT, _INTEGRATION_ROOT / "response-validation" / "helper", Path(__file__).parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from helper.pipeshub_client import PipeshubClient  # noqa: E402
from helper.second_user import second_user  # noqa: E402, F401 - fixture

from document_audit_support import (  # noqa: E402
    FETCH_CONFIG_SCOPE,
    STORAGE_TOKEN_SCOPE,
    DocumentClient,
    mint_scoped_token,
    scoped_jwt_secret,
)


@pytest.fixture(scope="session")
def document_client(pipeshub_client: PipeshubClient) -> DocumentClient:
    return DocumentClient(pipeshub_client)


@pytest.fixture(scope="session")
def scoped_secret(pipeshub_client: PipeshubClient, document_client: DocumentClient) -> str:
    """Skips when the run cannot sign service tokens the deployment accepts."""
    secret = scoped_jwt_secret()
    if not secret:
        pytest.skip(
            "SCOPED_JWT_SECRET is not set; /api/v1/document accepts only scoped service tokens"
        )
    # Node keeps its own secret in the config store once booted, so the env value can be
    # stale. A scope-less token tells them apart: the signature is checked before the scope.
    probe = document_client.update_app_config(token=mint_scoped_token(pipeshub_client.org_id, []))
    message = probe.json().get("error", {}).get("message") if probe.status_code == 401 else None
    if message != "Invalid scope":
        pytest.skip(
            "SCOPED_JWT_SECRET is not the deployment's scoped secret: a token signed with it "
            f"got {probe.status_code} {message!r} instead of 401 'Invalid scope'"
        )
    return secret


@pytest.fixture
def fetch_config_token(pipeshub_client: PipeshubClient, scoped_secret: str) -> str:
    """Fresh token with the scope POST /updateAppConfig requires."""
    return mint_scoped_token(
        pipeshub_client.org_id, [FETCH_CONFIG_SCOPE], pipeshub_client.acting_user_id
    )


@pytest.fixture
def storage_scope_token(pipeshub_client: PipeshubClient, scoped_secret: str) -> str:
    """Validly signed token for the /internal routes; the wrong scope for /updateAppConfig."""
    return mint_scoped_token(
        pipeshub_client.org_id, [STORAGE_TOKEN_SCOPE], pipeshub_client.acting_user_id
    )
