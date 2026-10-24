"""ServiceNow article Cannot Read: which readers an article keeps, loses or is skipped for."""

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest
from test_servicenow_connector import TestCriteriaDenyAndMatchAll as T
from test_servicenow_connector import (  # noqa: F401
    mock_config_service,
    mock_data_entities_processor,
    mock_data_store_provider,
    mock_logger,
    servicenow_connector,
)

from app.config.constants.arangodb import Connectors
from app.models.entities import AppUser
from app.models.permission import PermissionType
from app.sources.external.servicenow.models import KBKnowledge, TableAPIResponse


def _art(**kw) -> KBKnowledge:
    return KBKnowledge(sys_id="a1", short_description="t", kb_category="c1", kb_knowledge_base="kb1", **kw)


@pytest.mark.asyncio
async def test_the_kbs_own_cannot_read_still_applies_to_an_article_with_a_deny(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    T._wire(sc, {"kbcan": {"sys_id": "kbcan", "user": "u1,u2,u3"}, "kbdeny": {"sys_id": "kbdeny", "user": "u1"},
                 "deny1": {"sys_id": "deny1", "user": "u3"}}, {})
    sc._fetch_kb_permissions_from_criteria = AsyncMock(
        return_value={"read": ["kbcan"], "cannot_read": ["kbdeny"], "write": []})
    perms = await sc._article_deny_permissions(_art(cannot_read_user_criteria="deny1"), [], ["deny1"])
    assert T._users(perms) == ["u2"]


@pytest.mark.asyncio
async def test_with_the_override_property_the_own_allow_minus_deny_is_granted(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    sc.apply_article_read_criteria = True
    T._wire(sc, {"own": {"sys_id": "own", "user": "u2,u3,u4"}, "deny1": {"sys_id": "deny1", "user": "u3"}}, {})
    sc._fetch_kb_permissions_from_criteria = AsyncMock(side_effect=AssertionError("KB must not be read"))
    perms = await sc._article_deny_permissions(
        _art(can_read_user_criteria="own", cannot_read_user_criteria="deny1"), ["own"], ["deny1"])
    assert T._users(perms) == ["u2", "u4"]
    assert all(p.type == PermissionType.READ for p in perms)


@pytest.mark.asyncio
async def test_admin_owner_and_manager_keep_an_article_with_its_own_cannot_read(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    T._wire(sc, {"kbcan": {"sys_id": "kbcan", "user": "u1,u2,u3,adm,own,mgr"},
                 "deny1": {"sys_id": "deny1", "user": "u3,adm,own,mgr"}}, {})
    sc._fetch_kb_permissions_from_criteria = AsyncMock(return_value={"read": ["kbcan"], "cannot_read": [], "write": []})
    sc._kb_privileged_users = type(sc)._kb_privileged_users.__get__(sc)  # the real one
    sc._get_admin_users = AsyncMock(return_value=[AppUser(
        app_name=Connectors.SERVICENOW, connector_id="c", source_user_id="adm", email="adm@x.com", full_name="Admin")])
    sc._get_fresh_datasource.return_value.get_now_table_tableName.side_effect = _kb_row_then(
        sc._get_fresh_datasource.return_value.get_now_table_tableName.side_effect)
    perms = await sc._article_deny_permissions(_art(cannot_read_user_criteria="deny1"), [], ["deny1"])
    assert T._users(perms) == ["adm", "mgr", "own", "u1", "u2"]  # u3 (a plain denied user) stays denied


def _kb_row_then(real) -> Callable[..., Awaitable[TableAPIResponse]]:
    from test_servicenow_connector import _table_api_response

    async def table(tableName, **kw) -> TableAPIResponse:
        if tableName == "kb_knowledge_base":
            return _table_api_response([{"owner": "own", "kb_managers": "mgr,u9"}])
        return await real(tableName=tableName, **kw)

    return table


@pytest.mark.asyncio
@pytest.mark.parametrize(("deny", "rewrite"), [("deny1", True), (None, False)])
async def test_an_article_with_cannot_read_replaces_its_grants_and_its_attachments(servicenow_connector, deny, rewrite) -> None:  # noqa: F811
    from types import SimpleNamespace

    sc = servicenow_connector
    article = SimpleNamespace(rewrite_permissions=False, inherit_permissions=not deny,
                              record_group_type=None, external_record_group_id="kb1")
    attachment = SimpleNamespace(rewrite_permissions=False)
    sc._transform_to_article_webpage_record = lambda _: article
    sc._transform_to_attachment_file_record = lambda *_a, **_k: attachment
    sc._fetch_attachments_for_article = AsyncMock(return_value=[SimpleNamespace(sys_id="att1")])
    sc._article_deny_permissions = AsyncMock(return_value=[])
    sc._process_criteria_permissions = AsyncMock(return_value=[])

    updates = await sc._process_single_article(_art(cannot_read_user_criteria=deny))

    assert [u.record.rewrite_permissions for u in updates] == [rewrite, rewrite]


@pytest.mark.asyncio
async def test_a_rewrite_with_no_grants_is_still_written_so_stored_grants_go(servicenow_connector) -> None:  # noqa: F811
    from types import SimpleNamespace

    sc = servicenow_connector
    sc.data_entities_processor.on_new_records = AsyncMock()
    rewrite = SimpleNamespace(record=SimpleNamespace(rewrite_permissions=True), new_permissions=[], external_record_id="a1")
    plain = SimpleNamespace(record=SimpleNamespace(rewrite_permissions=False), new_permissions=[], external_record_id="a2")

    assert await sc._process_record_updates_batch([rewrite, plain]) == 1

    sc.data_entities_processor.on_new_records.assert_awaited_once_with([(rewrite.record, [])])


@pytest.mark.asyncio
async def test_articles_with_cannot_read_are_re_read_every_run_without_moving_the_checkpoint_back(servicenow_connector) -> None:  # noqa: F811
    from test_servicenow_connector import _table_api_response

    sc = servicenow_connector
    sc.article_sync_point = AsyncMock()
    sc.article_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": "2024-06-01 00:00:00"})
    sc.article_sync_point.update_sync_point = AsyncMock()
    queries: list[str] = []

    async def table(tableName, sysparm_query, **_) -> TableAPIResponse:
        queries.append(sysparm_query)
        if "ISNOTEMPTY" in sysparm_query:  # the re-read pass: an old article with Cannot Read
            return _table_api_response([{"sys_id": "old", "sys_updated_on": "2024-01-01 00:00:00"}])
        return _table_api_response([])

    sc._get_fresh_datasource = AsyncMock(return_value=AsyncMock(get_now_table_tableName=AsyncMock(side_effect=table)))
    sc._process_single_article = AsyncMock(return_value=[])

    await sc._sync_articles()

    assert len(queries) == 2
    assert "sys_updated_on>2024-06-01 00:00:00" in queries[0]
    assert "cannot_read_user_criteriaISNOTEMPTY" in queries[1] and "sys_updated_on<=2024-06-01 00:00:00" in queries[1]
    assert [c.args[0].sys_id for c in sc._process_single_article.await_args_list] == ["old"]
    sc.article_sync_point.update_sync_point.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_admin_read_skips_the_article_so_its_stored_admin_grants_stay(servicenow_connector) -> None:  # noqa: F811
    from types import SimpleNamespace

    from app.sources.external.servicenow.models import ServiceNowAPIError

    sc = servicenow_connector
    T._wire(sc, {"kbcan": {"sys_id": "kbcan", "user": "u1"}, "deny1": {"sys_id": "deny1", "user": "u3"}}, {})
    sc._fetch_kb_permissions_from_criteria = AsyncMock(return_value={"read": ["kbcan"], "cannot_read": [], "write": []})
    sc._kb_privileged_users = type(sc)._kb_privileged_users.__get__(sc)  # the real one
    sc._get_admin_users = AsyncMock(side_effect=ServiceNowAPIError(500, "fail", None))
    sc._transform_to_article_webpage_record = lambda _: SimpleNamespace(
        rewrite_permissions=False, inherit_permissions=False, record_group_type=None, external_record_group_id="kb1")
    sc._fetch_attachments_for_article = AsyncMock(return_value=[])
    sc.data_entities_processor.on_new_records = AsyncMock()

    updates = await sc._process_single_article(_art(cannot_read_user_criteria="deny1"))
    await sc._process_record_updates_batch(updates)

    assert updates == [], f"article written after a failed admin read: {updates}"
    assert sc._admin_source_ids is None, "an unread admin list must not be cached"
    sc.data_entities_processor.on_new_records.assert_not_awaited()
