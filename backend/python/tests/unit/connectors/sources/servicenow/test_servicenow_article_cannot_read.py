"""ServiceNow article Cannot Read: which readers an article keeps, loses or is skipped for."""

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest
from servicenow_fakes import (
    FakeServiceNow,
    article_row,
    migrated_sync_point,
    wire_listing,
)
from test_servicenow_connector import TestCriteriaDenyAndMatchAll as T

from app.config.constants.arangodb import AccessRule
from app.models.permission import PermissionType
from app.sources.external.servicenow.models import (
    AttachmentMetadata,
    KBKnowledge,
    TableAPIResponse,
)


def _art(**kw) -> KBKnowledge:
    return KBKnowledge(sys_id="a1", number="KB-a1", short_description="t", kb_category="c1", kb_knowledge_base="kb1", **kw)


async def _grants(sc, article: KBKnowledge) -> list:
    inherit, rule, grants = await sc._article_access(article)
    assert (inherit, rule) == (False, AccessRule.OPEN)  # Cannot Read: the users computed, no inheritance
    return grants


@pytest.mark.asyncio
async def test_the_kbs_own_cannot_read_still_applies_to_an_article_with_a_deny(servicenow_connector) -> None:
    sc = servicenow_connector
    T._wire(sc, {"kbcan": {"sys_id": "kbcan", "user": "u1,u2,u3"}, "kbdeny": {"sys_id": "kbdeny", "user": "u1"},
                 "deny1": {"sys_id": "deny1", "user": "u3"}}, {})
    sc._fetch_kb_permissions_from_criteria = AsyncMock(
        return_value={"read": ["kbcan"], "cannot_read": ["kbdeny"], "write": []})
    perms = await _grants(sc, _art(cannot_read_user_criteria="deny1"))
    assert T._users(perms) == ["u2"]


@pytest.mark.asyncio
async def test_with_the_override_property_readers_still_need_the_kb(servicenow_connector) -> None:
    # The property never makes an article's Can Read replace its KB's: u4 is not a KB reader.
    sc = servicenow_connector
    sc.apply_article_read_criteria = True
    T._wire(sc, {"kbcan": {"sys_id": "kbcan", "user": "u2,u3"}, "own": {"sys_id": "own", "user": "u2,u3,u4"},
                 "deny1": {"sys_id": "deny1", "user": "u3"}}, {})
    sc._fetch_kb_permissions_from_criteria = AsyncMock(return_value={"read": ["kbcan"], "cannot_read": [], "write": []})
    perms = await _grants(sc, _art(can_read_user_criteria="own", cannot_read_user_criteria="deny1"))
    assert T._users(perms) == ["u2"]
    assert all(p.type == PermissionType.READ for p in perms)


@pytest.mark.asyncio
async def test_admin_owner_and_manager_keep_an_article_with_its_own_cannot_read(servicenow_connector) -> None:
    sc = servicenow_connector
    T._wire(sc, {"kbcan": {"sys_id": "kbcan", "user": "u1,u2,u3,adm,own,mgr"},
                 "deny1": {"sys_id": "deny1", "user": "u3,adm,own,mgr"}}, {})
    sc._fetch_kb_permissions_from_criteria = AsyncMock(return_value={"read": ["kbcan"], "cannot_read": [], "write": []})
    sc._kb_privileged_users = type(sc)._kb_privileged_users.__get__(sc)  # the real one
    sc._get_admin_users = AsyncMock(return_value={"adm"})
    sc._get_fresh_datasource.return_value.get_now_table_tableName.side_effect = _kb_row_then(
        sc._get_fresh_datasource.return_value.get_now_table_tableName.side_effect)
    perms = await _grants(sc, _art(cannot_read_user_criteria="deny1"))
    assert T._users(perms) == ["adm", "mgr", "own", "u1", "u2"]  # u3 (a plain denied user) stays denied


def _kb_row_then(real) -> Callable[..., Awaitable[TableAPIResponse]]:
    from test_servicenow_connector import _table_api_response

    async def table(tableName, **kw) -> TableAPIResponse:
        if tableName == "kb_knowledge_base":  # one row, then the empty page that ends the listing
            first = "^NQ" not in kw["sysparm_query"]
            return _table_api_response([{"owner": "own", "kb_managers": "mgr,u9"}] if first else [])
        return await real(tableName=tableName, **kw)

    return table


@pytest.mark.asyncio
@pytest.mark.parametrize("deny", ["deny1", None])
async def test_every_article_replaces_its_grants_and_its_attachments(servicenow_connector, deny) -> None:
    # Not only Cannot Read articles: a criterion removed from any article must take its grant away.
    from types import SimpleNamespace

    sc = servicenow_connector
    article = SimpleNamespace(rewrite_permissions=False, inherit_permissions=not deny,
                              record_group_type=None, external_record_group_id="kb1", external_record_id="KB-a1")
    attachment = SimpleNamespace(rewrite_permissions=False)
    sc._transform_to_article_webpage_record = lambda *_: article
    sc._transform_to_attachment_file_record = lambda *_a, **_k: attachment
    sc._article_access = AsyncMock(return_value=(True, AccessRule.OPEN, []))

    updates = await sc._process_single_article(_art(cannot_read_user_criteria=deny), [AttachmentMetadata(
        sys_id="att1", file_name="f.pdf", content_type="application/pdf", size_bytes="1", table_sys_id="a1",
        table_name="kb_knowledge", sys_created_on="2024-01-01 00:00:00", sys_updated_on="2024-01-01 00:00:00")])

    assert [r.rewrite_permissions for r, _ in updates] == [True, True]


@pytest.mark.asyncio
async def test_a_rewrite_with_no_grants_is_still_written_so_stored_grants_go(servicenow_connector) -> None:
    sc = servicenow_connector
    wire_listing(sc, FakeServiceNow({"kb_knowledge": [article_row(1)]}))
    sc._article_access = AsyncMock(return_value=(False, AccessRule.OPEN, []))

    await sc._sync_articles()

    ((record, grants),) = sc.data_entities_processor.on_new_records.await_args.args[0]
    assert record.rewrite_permissions is True and grants == []


@pytest.mark.asyncio
async def test_articles_with_criteria_are_re_read_every_run_without_moving_the_checkpoint_back(servicenow_connector) -> None:
    from test_servicenow_connector import _table_api_response

    sc = servicenow_connector
    sc.article_sync_point = migrated_sync_point("2024-06-01 00:00:00")
    queries: list[str] = []

    async def table(tableName, sysparm_query="", **_) -> TableAPIResponse:
        # the access check (no stored state: the pass runs), and the feedback count
        if not sysparm_query or sysparm_query.startswith("ORDERBYDESC"):
            return _table_api_response([])
        queries.append(sysparm_query)
        # the re-read pass: an old article with Cannot Read, on its first page only
        if "ISNOTEMPTY" in sysparm_query and "^NQ" not in sysparm_query:
            return _table_api_response([{"sys_id": "old", "sys_updated_on": "2024-01-01 00:00:00"}])
        return _table_api_response([])

    sc._get_fresh_datasource = AsyncMock(return_value=AsyncMock(get_now_table_tableName=AsyncMock(side_effect=table)))
    sc._process_single_article = AsyncMock(return_value=[])

    await sc._sync_articles()

    assert len(queries) == 2  # the delta (empty), the re-read pass (one row): its body names no image
    # both passes split at DELTA_OVERLAP_SECONDS before the checkpoint
    assert "sys_updated_on>2024-05-31 23:58:00" in queries[0]
    assert "sys_updated_on<=2024-05-31 23:58:00" in queries[1]
    assert ("can_read_user_criteriaISNOTEMPTY^ORcannot_read_user_criteriaISNOTEMPTY^ORownership_groupISNOTEMPTY"
            in queries[1])
    assert [c.args[0].sys_id for c in sc._process_single_article.await_args_list] == ["old"]
    assert sc.article_sync_point.data["articles"] == {"last_sync_time": "2024-06-01 00:00:00"}  # not moved


@pytest.mark.asyncio
async def test_a_failed_admin_read_skips_the_article_so_its_stored_admin_grants_stay(servicenow_connector) -> None:
    from types import SimpleNamespace

    from app.sources.external.servicenow.models import ServiceNowAPIError

    sc = servicenow_connector
    T._wire(sc, {"kbcan": {"sys_id": "kbcan", "user": "u1"}, "deny1": {"sys_id": "deny1", "user": "u3"}}, {})
    sc._fetch_kb_permissions_from_criteria = AsyncMock(return_value={"read": ["kbcan"], "cannot_read": [], "write": []})
    sc._kb_privileged_users = type(sc)._kb_privileged_users.__get__(sc)  # the real one
    sc._get_admin_users = AsyncMock(side_effect=ServiceNowAPIError(500, "fail", None))
    sc._transform_to_article_webpage_record = lambda *_: SimpleNamespace(
        rewrite_permissions=False, inherit_permissions=False, record_group_type=None, external_record_group_id="kb1")

    # It raises, so _sync_articles skips the article (stored grants stay) and retries it next run.
    with pytest.raises(ServiceNowAPIError):
        await sc._process_single_article(_art(cannot_read_user_criteria="deny1"), [])

    assert sc._admin_source_ids is None, "an unread admin list must not be cached"
