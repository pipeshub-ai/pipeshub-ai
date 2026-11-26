"""Jira Cloud: with GET /users/search refused, the connector's own account (GET /myself) is still one of the users read.

The user who authenticated the connector reads through that account (``register_authenticated_source_user``). Refused, the
list is replaced by a lookup of each PipesHub e-mail, which does not find an account no PipesHub user has the e-mail of, so
the account's group and role memberships were saved without it and that user lost everything it reads in Jira.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.unit.connectors.sources.test_jira_cloud_connector import (
    _make_app_user,
    _make_connector,
    _make_mock_response,
    _then_empty,
)

ME, MY_EMAIL = "acc-sync", "Sync.Account@example.com"


def _connector(*pages, cached=(), me=ME, email=MY_EMAIL, active=()):
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=list(active))
    connector.data_entities_processor.get_all_app_users = AsyncMock(return_value=list(cached))
    connector._authenticated_jira_account_id, connector._authenticated_jira_email = me, email
    datasource = MagicMock()
    datasource.get_all_users = _then_empty(*pages)
    datasource.find_users = AsyncMock(return_value=_make_mock_response(200, []))
    connector._get_fresh_datasource = AsyncMock(return_value=datasource)
    connector.notify = AsyncMock()
    return connector


@pytest.mark.parametrize("status", [401, 403])
async def test_the_connectors_own_account_is_read_when_the_list_is_refused(status) -> None:
    users = await _connector(_make_mock_response(status))._fetch_users()
    assert [(u.source_user_id, u.email) for u in users] == [(ME, MY_EMAIL.lower())]


async def test_it_keeps_the_stored_name_of_the_account() -> None:
    cached = [_make_app_user(email=MY_EMAIL.lower(), account_id=ME, name="Sync Account")]
    users = await _connector(_make_mock_response(403), cached=cached)._fetch_users()
    assert [(u.source_user_id, u.full_name) for u in users] == [(ME, "Sync Account")]


async def test_it_is_read_once_when_a_pipeshub_user_has_its_email() -> None:
    connector = _connector(_make_mock_response(403), active=[_make_app_user(email=MY_EMAIL.lower(), account_id="")])
    connector._resolve_private_email_users = AsyncMock(
        side_effect=lambda _c, _u, resolved: resolved.update({ME: _make_app_user(email=MY_EMAIL.lower(), account_id=ME)}) or 1)
    users = await connector._fetch_users()
    assert [u.source_user_id for u in users] == [ME]


@pytest.mark.parametrize(("me", "email"), [(None, MY_EMAIL), (ME, None), (ME, "")])
async def test_an_account_not_known_in_full_is_not_made_up(me, email) -> None:
    assert await _connector(_make_mock_response(403), me=me, email=email)._fetch_users() == []


async def test_a_list_that_could_be_read_decides_alone() -> None:
    page = _make_mock_response(200)
    page.json = MagicMock(return_value=[{"accountId": "acc-1", "accountType": "atlassian", "active": True,
                                         "displayName": "One", "emailAddress": "one@example.com"}])
    users = await _connector(page)._fetch_users()
    assert [u.source_user_id for u in users] == ["acc-1"]
