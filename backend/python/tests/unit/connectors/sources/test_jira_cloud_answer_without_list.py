"""Jira Cloud: a 200 whose body lacks the list the operation answers with says nothing, so nothing stored is taken away for it.

An empty ``permissions`` (permission scheme), ``records`` (audit log), ``values`` (project search) or ``issues`` (issue search)
list is an answer. A body without one is not, and must not be read as "nobody has access", "nothing was deleted", "no
project" or "no issue changed". The same rule as a group's members without a ``values`` list (``_fetch_group_members``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.unit.connectors.sources.test_jira_cloud_connector import _make_connector, _make_mock_response

BODIES = [{}, {"permissions": None, "records": None, "values": None, "issues": None}, {"permissions": {}, "records": {}, "values": {}, "issues": {}}]


def _response(body):
    response = _make_mock_response(200)
    response.json = MagicMock(return_value=body)
    return response


@pytest.mark.parametrize("body", BODIES)
async def test_a_permission_scheme_without_a_grant_list_keeps_the_stored_access(body) -> None:
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector._call_with_retry = AsyncMock(side_effect=[_response({"id": 10}), _response(body)])
    assert await connector._fetch_project_permission_scheme("PROJ", {}, {}) is None
    assert "PROJ" not in connector._browse_issue_holders


@pytest.mark.parametrize("body", BODIES)
async def test_a_reindex_without_a_grant_list_reads_no_browse_holders(body) -> None:
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector._call_with_retry = AsyncMock(side_effect=[_response({"id": 10}), _response(body)])
    assert await connector._read_browse_issue_holders("PROJ") is None


async def test_an_empty_grant_list_is_still_a_scheme_that_grants_nobody() -> None:
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector._call_with_retry = AsyncMock(side_effect=[_response({"id": 10}), _response({"permissions": []})])
    assert await connector._fetch_project_permission_scheme("PROJ", {}, {}) == []


@pytest.mark.parametrize("body", BODIES)
async def test_an_audit_answer_without_records_does_not_end_the_window(body) -> None:
    connector = _make_connector()
    datasource = MagicMock()
    datasource.get_audit_records = AsyncMock(return_value=_response(body))
    connector._get_fresh_datasource = AsyncMock(return_value=datasource)
    keys, ok = await connector._fetch_deleted_issues_from_audit("2026-01-01", "2026-01-02")
    assert (keys, ok) == ([], False)


async def test_an_audit_answer_with_no_record_is_a_window_read() -> None:
    connector = _make_connector()
    datasource = MagicMock()
    datasource.get_audit_records = AsyncMock(return_value=_response({"records": [], "total": 0}))
    connector._get_fresh_datasource = AsyncMock(return_value=datasource)
    assert await connector._fetch_deleted_issues_from_audit("2026-01-01", "2026-01-02") == ([], True)


@pytest.mark.parametrize("body", [*BODIES, []])
async def test_a_project_search_without_a_project_list_is_not_an_account_that_sees_no_project(body) -> None:
    connector = _make_connector()
    connector._call_with_retry = AsyncMock(return_value=_response(body))
    with pytest.raises(Exception, match="no project list"):
        await connector._paginate_project_search()


async def test_an_empty_project_list_is_an_account_that_sees_no_project() -> None:
    connector = _make_connector()
    connector._call_with_retry = AsyncMock(return_value=_response({"values": [], "isLast": True, "total": 0}))
    assert await connector._paginate_project_search() == []


@pytest.mark.parametrize("body", [*BODIES, []])
async def test_an_issue_search_without_an_issue_list_is_not_a_project_with_no_issue_changed(body) -> None:
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector._search_issues_with_retry = AsyncMock(return_value=_response(body))
    with pytest.raises(Exception, match="no issue list"):
        async for _ in connector._fetch_issues_batched("PROJ", "p1", []):
            pass


async def test_an_empty_issue_list_ends_the_project() -> None:
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector._search_issues_with_retry = AsyncMock(return_value=_response({"issues": [], "isLast": True}))
    assert [b async for b in connector._fetch_issues_batched("PROJ", "p1", [])][0][:2] == ([], False)


@pytest.mark.parametrize("body", [*BODIES, []])
async def test_a_project_role_without_an_actor_list_is_not_saved_as_a_role_nobody_holds(body) -> None:
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector._call_with_retry = AsyncMock(side_effect=[_response({"Developers": "https://x/rest/api/3/project/PROJ/role/10"}), _response(body)])
    key, roles, failed = await connector._fetch_project_roles("PROJ", {}, {}, {})
    assert (key, roles, failed) == ("PROJ", [], True)
    assert connector.full_sync_coverage().complete is False


async def test_a_project_role_with_no_actor_is_a_role_nobody_holds() -> None:
    connector = _make_connector()
    connector.data_source = MagicMock()
    connector._call_with_retry = AsyncMock(side_effect=[_response({"Developers": "https://x/rest/api/3/project/PROJ/role/10"}), _response({"actors": []})])
    key, roles, failed = await connector._fetch_project_roles("PROJ", {}, {}, {})
    assert (key, [(r.source_role_id, m) for r, m in roles], failed) == ("PROJ", [("PROJ_10", [])], False)
