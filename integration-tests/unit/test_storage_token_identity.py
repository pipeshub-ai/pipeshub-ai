"""The storage suite's service tokens must name a real user.

The suite logs in with an OAuth client_credentials token. Node puts the app's
client id, a UUID, in that token's ``userId``, and the account it acts as in
``createdBy``. The storage routes turn a service token's ``userId`` into a
Mongo ObjectId, so a token minted with the client id answered every upload,
placeholder and new version with a 500 ("input must be a 24 character hex
string"): 99 of the storage suite's 130 tests failed on the nightly.
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import pytest
from jose import jwt

_IT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_IT / "storage"))

from pipeshub_client import PipeshubClient  # noqa: E402
from storage_client import STORAGE_TOKEN_SCOPE, mint_storage_token  # noqa: E402

pytestmark = pytest.mark.unit

ORG = "65f0c0ffee0000000000aaaa"
ADMIN = "65f0c0ffee0000000000bbbb"
SERVICE_ACCOUNT = "65f0c0ffee0000000000cccc"
CLIENT_ID = "3b241101-e2bb-4255-8caf-4136c566a962"


def _client(**claims: object) -> PipeshubClient:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    client = PipeshubClient(base_url="http://pipeshub.test")
    client._access_token = f"e30.{body}.sig"
    client._token_expires_at = time.time() + 3600
    return client


def test_a_client_credentials_token_acts_as_its_account_not_its_client_id() -> None:
    client = _client(userId=CLIENT_ID, client_id=CLIENT_ID, createdBy=ADMIN, orgId=ORG, tokenType="oauth")
    assert client.user_id == CLIENT_ID
    assert client.acting_user_id == ADMIN


def test_an_app_pointed_at_a_service_account_acts_as_that_account() -> None:
    client = _client(userId=CLIENT_ID, client_id=CLIENT_ID, createdBy=SERVICE_ACCOUNT, orgId=ORG)
    assert client.acting_user_id == SERVICE_ACCOUNT


def test_a_token_that_carries_a_user_keeps_it() -> None:
    """An authorization-code token names its user; createdBy must not replace it."""
    client = _client(userId=ADMIN, client_id=CLIENT_ID, createdBy=SERVICE_ACCOUNT, orgId=ORG)
    assert client.acting_user_id == ADMIN


def test_the_storage_token_names_an_object_id_user(monkeypatch) -> None:
    monkeypatch.setenv("SCOPED_JWT_SECRET", "unit-test-scoped-secret")
    client = _client(userId=CLIENT_ID, client_id=CLIENT_ID, createdBy=ADMIN, orgId=ORG, tokenType="oauth")
    token = mint_storage_token(client.org_id, client.acting_user_id)
    claims = jwt.decode(token, "unit-test-scoped-secret", algorithms=["HS256"])
    assert claims["userId"] == ADMIN
    assert claims["orgId"] == ORG
    assert claims["scopes"] == [STORAGE_TOKEN_SCOPE]
    # What mongoose.Types.ObjectId accepts from a string: 24 hex characters.
    assert len(claims["userId"]) == 24 and int(claims["userId"], 16) >= 0


def test_the_suite_mints_with_the_acting_user() -> None:
    """Both places the suite mints a token use it, not the raw userId claim."""
    for path in (_IT / "storage" / "storage_client.py", _IT / "storage" / "conftest.py"):
        source = path.read_text(encoding="utf-8")
        assert "mint_storage_token(" in source
        assert ".user_id)" not in source, f"{path.name} mints a storage token with the raw userId claim"
