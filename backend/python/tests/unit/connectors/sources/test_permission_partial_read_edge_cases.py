"""Edge cases of keeping stored access when a permission read fails or returns part of the data."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_salesforce_connector import _make_connector
from test_servicenow_connector import (  # noqa: F401
    _table_api_response,
    mock_config_service,
    mock_data_entities_processor,
    mock_data_store_provider,
    mock_logger,
    servicenow_connector,
)

from app.models.permission import PermissionType
from app.sources.external.servicenow.models import KBKnowledge, TableAPIResponse


def _composite(items) -> MagicMock:
    return MagicMock(success=True, data={"compositeResponse": items}, error=None)


async def _drain(connector, incomplete) -> list[tuple[str, str, str]]:
    return [
        x async for x in connector._iter_record_access(
            user_id="005000000000001", user_email="a@x.com", record_ids=["006000000000001"],
            api_version="59.0", incomplete=incomplete,
        )
    ]


@pytest.mark.asyncio
async def test_sf_a_non_200_composite_item_marks_the_user_incomplete() -> None:
    c = _make_connector()
    c.data_source = MagicMock()
    c.data_source.composite = AsyncMock(return_value=_composite([{"httpStatusCode": 503, "body": []}]))
    inc: set = set()
    assert await _drain(c, inc) == [] and inc == {"a@x.com"}


@pytest.mark.asyncio
async def test_sf_an_invalid_user_id_marks_the_user_incomplete() -> None:
    c = _make_connector()
    inc: set = set()
    out = [x async for x in c._iter_record_access(
        user_id="bad id'", user_email="a@x.com", record_ids=["006000000000001"], api_version="59.0", incomplete=inc)]
    assert out == [] and inc == {"a@x.com"}


@pytest.mark.asyncio
async def test_sf_a_raising_composite_call_marks_the_user_incomplete() -> None:
    c = _make_connector()
    c.data_source = MagicMock()
    c.data_source.composite = AsyncMock(side_effect=RuntimeError("socket reset"))
    inc: set = set()
    assert await _drain(c, inc) == [] and inc == {"a@x.com"}


@pytest.mark.asyncio
async def test_sn_an_article_cannot_read_denies_users_the_knowledge_base_grants(servicenow_connector) -> None:  # noqa: F811
    """Article-level Cannot Read must bite even when the article has no Can Read of its own
    (ServiceNow docs: Can Read at KB + Cannot Read at article = denied). The article stops
    inheriting and gets the explicit effective set: KB allowed minus article denied."""
    from test_servicenow_connector import TestCriteriaDenyAndMatchAll as T

    art = KBKnowledge(sys_id="a1", short_description="t", kb_category="cat1", kb_knowledge_base="kb1",
                      cannot_read_user_criteria="deny1")
    sc = servicenow_connector
    T._wire(
        sc,
        {"kbcan": {"sys_id": "kbcan", "group": "g"}, "deny1": {"sys_id": "deny1", "user": "u2"}},
        {("sys_user_grmember", "g"): ["u1", "u2"]},
    )
    sc._fetch_attachments_for_article = AsyncMock(return_value=[])
    sc._fetch_kb_permissions_from_criteria = AsyncMock(return_value={"read": ["kbcan"], "cannot_read": [], "write": []})
    updates = await sc._process_single_article(art)
    assert updates[0].record.inherit_permissions is False
    assert [p.email for p in updates[0].new_permissions if p.email] == ["u1@x.com"]


@pytest.mark.asyncio
async def test_sn_an_article_cannot_read_that_cannot_be_read_skips_the_article(servicenow_connector) -> None:  # noqa: F811
    from test_servicenow_connector import TestCriteriaDenyAndMatchAll as T

    from app.sources.external.servicenow.models import ServiceNowAPIError

    art = KBKnowledge(sys_id="a1", short_description="t", kb_category="cat1", kb_knowledge_base="kb1",
                      cannot_read_user_criteria="deny1")
    sc = servicenow_connector
    T._wire(sc, {}, {})
    sc._get_fresh_datasource.return_value.get_now_table_tableName = AsyncMock(
        side_effect=ServiceNowAPIError(500, "boom", None))
    sc._fetch_attachments_for_article = AsyncMock(return_value=[])
    sc._fetch_kb_permissions_from_criteria = AsyncMock(return_value={"read": ["kbcan"], "cannot_read": [], "write": []})
    assert await sc._process_single_article(art) == []


@pytest.mark.asyncio
async def test_sf_a_failed_read_of_record_group_access_marks_the_user_incomplete() -> None:
    c = _make_connector()
    c.data_source = MagicMock()
    c.data_source.composite = AsyncMock(return_value=MagicMock(success=False, data=None, error="503"))
    inc: set = set()
    await c._sync_permissions_for_user(
        user={"Id": "005000000000001", "Email": "a@x.com"}, salesforce_external_ids=[],
        salesforce_record_group_external_ids=["001000000000001"], api_version="59.0",
        semaphore=asyncio.Semaphore(1), incomplete=inc)
    assert inc == {"a@x.com"}


@pytest.mark.asyncio
async def test_sn_a_deny_criteria_without_match_all_denies_any_one_field(servicenow_connector) -> None:  # noqa: F811
    """Cannot Read with group G and user U2 (no Match All) denies members of G OR U2; an
    intersection here would under-deny (over-share)."""
    from test_servicenow_connector import TestCriteriaDenyAndMatchAll as T

    T._wire(
        servicenow_connector,
        {"can": {"sys_id": "can", "user": "u1,u2,u3,u4"}, "deny": {"sys_id": "deny", "group": "g", "user": "u2"}},
        {("sys_user_grmember", "g"): ["u3"]},
    )
    perms = await servicenow_connector._process_criteria_permissions(
        ["can"], PermissionType.READ, deny_criteria_ids=["deny"])
    assert T._users(perms) == ["u1", "u4"]


@pytest.mark.asyncio
async def test_sn_an_unreadable_deny_criteria_row_read_raises(servicenow_connector) -> None:  # noqa: F811
    from test_servicenow_connector import TestCriteriaDenyAndMatchAll as T

    from app.connectors.sources.servicenow.servicenow.connector import (
        _CriteriaUnreadable,
    )
    from app.sources.external.servicenow.models import ServiceNowAPIError

    T._wire(servicenow_connector, {"can": {"sys_id": "can", "group": "g"}}, {})
    real = servicenow_connector._get_fresh_datasource.return_value.get_now_table_tableName.side_effect

    async def flaky(tableName, sysparm_query="", **kw) -> TableAPIResponse:
        if tableName == "user_criteria" and "deny" in sysparm_query:
            raise ServiceNowAPIError(500, "boom", None)
        return await real(tableName=tableName, sysparm_query=sysparm_query, **kw)

    servicenow_connector._get_fresh_datasource.return_value.get_now_table_tableName.side_effect = flaky
    with pytest.raises(_CriteriaUnreadable):
        await servicenow_connector._process_criteria_permissions(["can"], PermissionType.READ, deny_criteria_ids=["deny"])


@pytest.mark.asyncio
async def test_sn_the_kb_loop_passes_the_cannot_read_criteria_to_the_read_grants(servicenow_connector) -> None:  # noqa: F811
    from unittest.mock import patch

    from test_servicenow_connector import _table_api_response as resp

    sc = servicenow_connector
    sc.data_entities_processor.on_new_record_groups = AsyncMock()
    calls = []

    async def spy(criteria_ids, permission_type, deny_criteria_ids=None) -> list[Any]:
        calls.append((list(criteria_ids), permission_type, deny_criteria_ids))
        return []

    with patch.object(sc, "_get_fresh_datasource", new_callable=AsyncMock) as ds, \
         patch.object(sc, "_transform_to_kb_record_group", return_value=MagicMock(id="rg-1")), \
         patch.object(sc, "_fetch_kb_permissions_from_criteria", new_callable=AsyncMock,
                      return_value={"read": ["c"], "cannot_read": ["d"], "write": []}), \
         patch.object(sc, "_process_criteria_permissions", side_effect=spy):
        ds.return_value.get_now_table_tableName = AsyncMock(
            return_value=resp([{"sys_id": "kb1", "title": "KB 1", "owner": "o1"}]))
        await sc._sync_knowledge_bases([])
    assert (["c"], PermissionType.READ, ["d"]) in calls


@pytest.mark.asyncio
async def test_sn_an_article_with_cannot_read_takes_the_explicit_grant_path(servicenow_connector) -> None:  # noqa: F811
    art = KBKnowledge(sys_id="a1", short_description="t", kb_category="c1", can_read_user_criteria="c",
                      cannot_read_user_criteria="d1, d2")
    sc = servicenow_connector
    sc._fetch_attachments_for_article = AsyncMock(return_value=[])
    sc._article_deny_permissions = AsyncMock(return_value=[])
    await sc._process_single_article(art)
    sc._article_deny_permissions.assert_awaited_once_with(art, ["c"], ["d1", "d2"])


@pytest.mark.asyncio
async def test_sn_the_article_query_requests_cannot_read_user_criteria(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    sc.article_sync_point = AsyncMock()
    sc.article_sync_point.read_sync_point = AsyncMock(return_value={})
    ds = AsyncMock()
    ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
    sc._get_fresh_datasource = AsyncMock(return_value=ds)
    await sc._sync_articles()
    fields = [c.kwargs.get("sysparm_fields", "") for c in ds.get_now_table_tableName.await_args_list]
    assert any("cannot_read_user_criteria" in f for f in fields)


@pytest.mark.asyncio
async def test_sn_run_sync_resets_the_user_listing_cache(servicenow_connector) -> None:  # noqa: F811
    from unittest.mock import patch

    sc = servicenow_connector
    sc.servicenow_client = MagicMock()
    sc._email_by_source_id = {"stale": "stale@x.com"}
    with patch.object(sc, "_sync_users_and_groups", new_callable=AsyncMock), \
         patch.object(sc, "_get_admin_users", new_callable=AsyncMock, return_value=[]), \
         patch.object(sc, "_sync_knowledge_bases", new_callable=AsyncMock), \
         patch.object(sc, "_sync_categories", new_callable=AsyncMock), \
         patch.object(sc, "_sync_articles", new_callable=AsyncMock):
        await sc.run_sync()
    assert sc._email_by_source_id is None


@pytest.mark.asyncio
async def test_sn_an_article_with_own_allow_and_a_deny_narrows_the_knowledge_base_set(servicenow_connector) -> None:  # noqa: F811
    from test_servicenow_connector import TestCriteriaDenyAndMatchAll as T

    art = KBKnowledge(sys_id="a1", short_description="t", kb_category="c1", kb_knowledge_base="kb1",
                      can_read_user_criteria="own", cannot_read_user_criteria="deny1")
    sc = servicenow_connector
    T._wire(
        sc,
        {"kbcan": {"sys_id": "kbcan", "user": "u1,u2,u3"}, "own": {"sys_id": "own", "user": "u2,u3,u4"},
         "deny1": {"sys_id": "deny1", "user": "u3"}},
        {},
    )
    sc._fetch_kb_permissions_from_criteria = AsyncMock(return_value={"read": ["kbcan"], "cannot_read": [], "write": []})
    perms = await sc._article_deny_permissions(art, ["own"], ["deny1"])
    assert T._users(perms) == ["u2"]  # (KB u1,u2,u3 - u3) and own u2,u3,u4
