"""Shared OAuth app lookups do not write the credential-bearing config, or the
id read out of it, to a log.

The ``oauthConfigId`` is read from the same ``auth`` dict that holds the client
secret. Log lines name the connector (its type or instance id) instead, which
is enough to find the record and carries nothing from the credential dict.
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.api import router as router_module
from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.connectors.sources.dropbox_individual.connector import (
    DropboxIndividualConnector,
)
from app.connectors.sources.google.gmail.individual.connector import (
    GoogleGmailIndividualConnector,
)

OAUTH_ID = "OAUTHID-SENTINEL-91c2"
SECRET = "SECRET-SENTINEL-4be7"
INSTANCE_NAME = "NAME-SENTINEL-d05a"


def _config_service() -> AsyncMock:
    svc = AsyncMock()
    svc.get_config = AsyncMock(
        return_value={
            "credentials": {"access_token": "t", "refresh_token": "r", "isTeam": True},
            "auth": {"oauthConfigId": OAUTH_ID, "clientSecret": SECRET},
        }
    )
    return svc


def _processor() -> MagicMock:
    proc = MagicMock()
    proc.org_id = "org-1"
    return proc


def _make(cls: type, app_path: str) -> Any:  # noqa: ANN401
    with patch(app_path):
        return cls(
            logger=logging.getLogger("test.oauth-log-hygiene"),
            data_entities_processor=_processor(),
            data_store_provider=MagicMock(),
            config_service=_config_service(),
            connector_id="conn-visible-1",
            scope="personal",
            created_by="user-1",
        )


def _assert_clean(caplog, *, expect: str) -> None:
    for sentinel in (OAUTH_ID, SECRET, INSTANCE_NAME):
        assert sentinel not in caplog.text
    assert expect in caplog.text, "the line was not logged; the test would pass vacuously"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls,app_path",
    [
        (DropboxConnector, "app.connectors.sources.dropbox.connector.DropboxApp"),
        (
            DropboxIndividualConnector,
            "app.connectors.sources.dropbox_individual.connector.DropboxIndividualApp",
        ),
        (
            GoogleGmailIndividualConnector,
            "app.connectors.sources.google.gmail.individual.connector.GmailIndividualApp",
        ),
    ],
)
async def test_init_without_linked_app_logs_connector_not_oauth_id(cls, app_path, caplog) -> None:
    conn = _make(cls, app_path)
    conn._fetch_oauth_config_by_id = AsyncMock(return_value=None)
    with caplog.at_level(logging.DEBUG):
        assert await conn.init() is False
    _assert_clean(caplog, expect="conn-visible-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls,app_path,client_patch",
    [
        (
            DropboxConnector,
            "app.connectors.sources.dropbox.connector.DropboxApp",
            "app.connectors.sources.dropbox.connector.DropboxClient.build_with_config",
        ),
        (
            DropboxIndividualConnector,
            "app.connectors.sources.dropbox_individual.connector.DropboxIndividualApp",
            "app.connectors.sources.dropbox_individual.connector.DropboxClient.build_with_config",
        ),
    ],
)
async def test_init_with_linked_app_logs_connector_not_oauth_id(
    cls, app_path, client_patch, caplog
) -> None:
    conn = _make(cls, app_path)
    conn._fetch_oauth_config_by_id = AsyncMock(
        return_value={"config": {"clientId": "key", "clientSecret": SECRET}}
    )
    with patch(client_patch, new_callable=AsyncMock, side_effect=RuntimeError("stop")):
        with caplog.at_level(logging.DEBUG):
            await conn.init()
    _assert_clean(caplog, expect="conn-visible-1")


def _token_service() -> TokenRefreshService:
    return TokenRefreshService(AsyncMock(), AsyncMock())


@pytest.mark.asyncio
async def test_shared_config_not_in_list_does_not_log_id(caplog) -> None:
    svc = _token_service()
    svc.configuration_service.get_config = AsyncMock(
        return_value=[{"_id": "someone-else", "config": {"clientSecret": SECRET}}]
    )
    with caplog.at_level(logging.DEBUG):
        assert await svc._fetch_shared_oauth_config(OAUTH_ID, "Dropbox") == {}
    _assert_clean(caplog, expect="not found in the list")


@pytest.mark.asyncio
async def test_shared_config_fetch_error_logs_type_not_message(caplog) -> None:
    svc = _token_service()
    svc.configuration_service.get_config = AsyncMock(
        side_effect=ValueError(f"cannot read {OAUTH_ID} {SECRET}")
    )
    with caplog.at_level(logging.DEBUG):
        assert await svc._fetch_shared_oauth_config(OAUTH_ID, "Dropbox") == {}
    _assert_clean(caplog, expect="ValueError")


@pytest.mark.asyncio
async def test_build_oauth_flow_config_not_found_does_not_log_id(caplog) -> None:
    log = logging.getLogger("test.oauth-log-hygiene.router")
    with patch.object(
        router_module,
        "resolve_shared_oauth_config_for_flow",
        new=AsyncMock(return_value=None),
    ):
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(HTTPException):
                await router_module._build_oauth_flow_config(
                    {"oauthConfigId": OAUTH_ID, "clientSecret": SECRET},
                    "Dropbox",
                    "org-1",
                    AsyncMock(),
                    log,
                )
    _assert_clean(caplog, expect="Dropbox")


@pytest.mark.asyncio
@pytest.mark.parametrize("stored_id", [OAUTH_ID, None])
@pytest.mark.parametrize("existing_ids", [[OAUTH_ID], []])
async def test_pre_validation_debug_lines_do_not_log_id_or_name(
    stored_id, existing_ids, caplog
) -> None:
    log = logging.getLogger("test.oauth-log-hygiene.prevalidate")
    config = {
        "auth": {
            "clientId": "cid",
            "clientSecret": SECRET,
            "oauthInstanceName": INSTANCE_NAME,
        }
    }
    existing = [
        {"_id": i, "orgId": "org-1", "oauthInstanceName": "Other"} for i in existing_ids
    ]
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(return_value=existing)
    with (
        patch.object(router_module, "_check_oauth_app_urls"),
        patch.object(
            router_module, "_get_oauth_field_names_from_registry", return_value=["clientId"]
        ),
        patch.object(router_module, "_check_oauth_name_conflict"),
    ):
        with caplog.at_level(logging.DEBUG):
            await router_module._validate_admin_oauth_config_before_creation(
                "Dropbox", config, stored_id, "inst", "org-1", config_service, log
            )
    _assert_clean(caplog, expect="Pre-validation")
