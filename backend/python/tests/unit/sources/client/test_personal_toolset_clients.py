"""A client built for a personal action connects only to the public address that was checked;
one built for an organization's action is unchanged."""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.confluence.confluence import ConfluenceClient
from app.sources.client.drupal_wiki.drupal_wiki import DrupalWikiClient
from app.sources.client.jira.jira import JiraClient
from app.sources.client.salesforce.salesforce import SalesforceClient

LOG = logging.getLogger(__name__)


def _record(scope: str | None, **auth: str) -> dict[str, Any]:
    record: dict[str, Any] = {"_id": "inst-1", "orgId": "org-1", "createdBy": "u-1", "auth": auth}
    if scope:
        record["scope"] = scope
    return record


def _sign_in(auth_type: str = "API_TOKEN", **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"instanceId": "inst-1", "orgId": "org-1", "isAuthenticated": True, "authType": auth_type, **extra}


def _public_only(client: Any) -> bool:  # noqa: ANN401
    return client.get_client()._public_only


@pytest.mark.parametrize(("scope", "pinned"), [("personal", True), ("org", False), (None, False)])
class TestAtlassianAndDrupal:
    async def test_jira(self, scope: str | None, pinned: bool) -> None:
        record = _record(scope, baseUrl="https://me.atlassian.net")
        with patch("app.edition_config.get_toolset_by_id", AsyncMock(return_value=record)):
            client = await JiraClient.build_from_toolset(
                _sign_in(auth={"email": "me@example.com", "apiToken": "t"}), LOG, MagicMock(),
            )
        assert _public_only(client) is pinned

    async def test_confluence(self, scope: str | None, pinned: bool) -> None:
        record = _record(scope, baseUrl="https://me.atlassian.net/wiki")
        with patch("app.edition_config.get_toolset_by_id", AsyncMock(return_value=record)):
            client = await ConfluenceClient.build_from_toolset(
                _sign_in(auth={"email": "me@example.com", "apiToken": "t"}), LOG, MagicMock(),
            )
        assert _public_only(client) is pinned

    async def test_drupal_wiki(self, scope: str | None, pinned: bool) -> None:
        record = _record(scope, baseUrl="https://wiki.example.com")
        with patch("app.edition_config.get_toolset_by_id", AsyncMock(return_value=record)):
            client = await DrupalWikiClient.build_from_toolset(_sign_in(auth={"apiToken": "t"}), LOG, MagicMock())
        assert _public_only(client) is pinned


class TestSalesforce:
    async def _build(self, record: dict[str, Any] | None, **sign_in: Any) -> SalesforceClient:  # noqa: ANN401
        oauth = AsyncMock(return_value={"instance_url": "https://me.my.salesforce.com"})
        config = _sign_in("OAUTH", credentials={"access_token": "a"}, **sign_in)
        with patch("app.edition_config.get_toolset_by_id", AsyncMock(return_value=record)), \
             patch("app.edition_config.get_oauth_credentials_for_toolset", oauth):
            return await SalesforceClient.build_from_toolset(config, LOG, MagicMock())

    async def test_a_personal_one_is_pinned(self) -> None:
        assert _public_only(await self._build(_record("personal"))) is True

    async def test_an_org_one_is_not(self) -> None:
        assert _public_only(await self._build(_record(None))) is False

    async def test_a_sign_in_marked_personal_is_pinned_too(self) -> None:
        assert _public_only(await self._build(_record(None), scope="personal")) is True

    async def test_a_personal_one_withheld_for_its_address_is_not_built(self) -> None:
        # get_toolset_by_id returns None for a personal action whose address turned private.
        with pytest.raises(ValueError, match="not found"):
            await self._build(None, scope="personal")
