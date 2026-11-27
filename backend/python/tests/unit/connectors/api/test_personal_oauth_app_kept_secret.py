"""A person's own OAuth app keeps its client secret on the host it was saved for.

Changing a personal app's instance URL without typing the client secret again would send
the saved secret to the new host on the next sign-in, so the save is refused.
"""

import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

# Importing the connector registers its OAuth fields (client ID, secret and instance URL).
import app.connectors.sources.gitlab.connector  # noqa: F401
from app.connectors.api import router as router_module
from app.utils.oauth_config import CREDENTIAL_REENTRY_MESSAGE, OAUTH_APP_OWNER_FIELD
from tests.support.in_memory_config import InMemoryConfigService

ORG_ID = "org-1"
ALICE = "user-alice"
TYPE = "GitLab Personal"
SECRET = "alice-secret"


def _store() -> InMemoryConfigService:
    store = InMemoryConfigService()
    store.values[router_module._get_oauth_config_path(TYPE)] = [{
        "_id": "alice-app",
        "oauthInstanceName": "My GitLab",
        "orgId": ORG_ID,
        "createdBy": ALICE,
        OAUTH_APP_OWNER_FIELD: ALICE,
        "config": {"clientId": "alice-client", "clientSecret": SECRET, "instanceUrl": "https://gitlab.alice.example"},
    }]
    return store


async def _save(store: InMemoryConfigService, auth: dict) -> object:
    with patch.object(router_module, "_require_public_oauth_app_addresses", AsyncMock()), \
         patch.object(router_module, "_update_oauth_infrastructure_fields", AsyncMock()):
        return await router_module._handle_oauth_config_creation(
            connector_type=TYPE, auth_config={"oauthConfigId": "alice-app", **auth}, instance_name="My GitLab",
            user_id=ALICE, org_id=ORG_ID, is_admin=False, config_service=store, oauth_config_id=None,
            auth_type="OAUTH", base_url="https://pipeshub.example", logger=logging.getLogger("t"),
            connector_scope="personal",
        )


def _app(store: InMemoryConfigService) -> dict:
    return store.values[router_module._get_oauth_config_path(TYPE)][0]


@pytest.mark.asyncio
async def test_a_new_instance_url_without_the_secret_is_refused() -> None:
    store = _store()
    with pytest.raises(HTTPException) as exc:
        await _save(store, {"clientId": "alice-client", "instanceUrl": "https://elsewhere.example"})
    assert exc.value.status_code == 400
    assert exc.value.detail == CREDENTIAL_REENTRY_MESSAGE
    assert _app(store)["config"]["instanceUrl"] == "https://gitlab.alice.example"


@pytest.mark.asyncio
async def test_a_new_instance_url_with_the_secret_typed_again_is_saved() -> None:
    store = _store()
    await _save(store, {"clientId": "alice-client", "clientSecret": SECRET, "instanceUrl": "https://elsewhere.example"})
    assert _app(store)["config"]["instanceUrl"] == "https://elsewhere.example"
