"""
Fixtures for OAuth client API integration tests.

Auth comes from the root ``conftest.py``'s ``pipeshub_client`` fixture:
``PipeshubClient`` already handles ``POST /api/v1/oauth2/token`` (grant=client_credentials),
caches the JWT, and refreshes before expiry. The bootstrapping path
(``local_auth.obtain_local_oauth_credentials``) populates CLIENT_ID/CLIENT_SECRET
from a password login when those env vars aren't pre-set.
"""

from __future__ import annotations

import pytest
import requests

from pipeshub_client import PipeshubClient


@pytest.fixture(scope="session")
def oauth_api_base_url(pipeshub_client: PipeshubClient) -> str:
    return pipeshub_client.base_url


@pytest.fixture
def auth_headers(pipeshub_client: PipeshubClient) -> dict[str, str]:
    """``Authorization: Bearer <client-credentials JWT>`` + JSON content type.

    Function-scoped on purpose: ``PipeshubClient._headers()`` calls
    ``_ensure_access_token()`` which refreshes the JWT on each call when it's
    near expiry. Caching at session scope would freeze the very first token
    dict for the whole run, masking expiry-driven 401s as ``expected
    unauthorized`` once schema validation sees the error envelope.
    """
    return pipeshub_client._headers()


@pytest.fixture
def http() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    return s
