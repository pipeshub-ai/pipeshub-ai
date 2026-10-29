"""ServiceNow record identity and lifecycle: one record per article number, following its current version;
what ServiceNow search does not show is not indexed; deletes, expiry, removed attachments, gone knowledge
bases and categories; and the one-time move of records stored under a version's sys_id.

The Table API is ``FakeServiceNow``; the stored records are ``FakeRecordStore``.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from servicenow_fakes import (
    FakeRecordStore,
    FakeServiceNow,
    MemorySyncPoint,
    migrated_sync_point,
    status_error,
    stored_record,
)
from test_servicenow_batch_a import INSTANCE, T0, _article, _attachment
from test_servicenow_connector import (  # noqa: F401
    mock_config_service,
    mock_data_entities_processor,
    mock_data_store_provider,
    mock_logger,
    servicenow_connector,
)

from app.config.constants.arangodb import Connectors
from app.models.entities import AppUser, RecordGroupType
from app.sources.external.servicenow.models import (
    AttachmentMetadata,
    KBKnowledge,
    TableAPIRecord,
)

KB1 = "KB0000001"
LATER = "2024-06-02 00:00:00"  # after the checkpoint T0
EARLIER = "2024-05-01 00:00:00"
V1, V2 = f"{1:032x}", f"{2:032x}"  # sys_ids of two versions, as an instance makes them


def _today(days: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


def _wire(sc, fake: FakeServiceNow, *records: SimpleNamespace, sync_point: MemorySyncPoint | None = None) -> FakeRecordStore:
    sc._get_fresh_datasource = AsyncMock(return_value=fake)
    sc.instance_url = INSTANCE
    sc.article_sync_point = sync_point if sync_point is not None else migrated_sync_point()
    return FakeRecordStore(*records).attach(sc.data_entities_processor)


def _users_by_source_id(sc) -> None:
    sc.data_entities_processor.get_user_by_source_id = AsyncMock(side_effect=lambda source_id, connector_id: AppUser(
        app_name=Connectors.SERVICENOW, connector_id=connector_id, source_user_id=source_id,
        email=f"{source_id}@x.com", full_name=source_id))


def _row(n: int = 1, **fields: str) -> TableAPIRecord:
    """An article row with every field the sync reads."""
    return TableAPIRecord(**{"author": "", "can_read_user_criteria": "", "cannot_read_user_criteria": "",
                             **_article(n, **fields)})


def _kb(sys_id: str, **fields: str) -> dict:
    return {"sys_id": sys_id, "title": sys_id.upper(), "description": "", "owner": "", "kb_managers": "",
            "active": "true", "sys_created_on": T0, "sys_updated_on": T0, **fields}


def _category(sys_id: str, parent: str, **fields: str) -> dict:
    return {"sys_id": sys_id, "label": sys_id.upper(), "parent_id": parent, "sys_updated_on": T0, **fields}


def _groups_written(sc) -> list[str]:
    return sorted(g.external_group_id for c in sc.data_entities_processor.on_new_record_groups.await_args_list
                  for g, _ in c.args[0])


# --- the rule: only the rows ServiceNow search shows ---------------------------------------------


@pytest.mark.asyncio
async def test_only_rows_search_shows_are_indexed(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [
        _article(1),
        _article(2, valid_to=_today()),  # expires at the end of today
        _article(3, workflow_state="retired"),
        _article(4, workflow_state="outdated"),
        _article(5, workflow_state="draft"),
        _article(6, workflow_state="review"),
        _article(7, active="false"),
        _article(8, valid_to=_today(-1)),
        _article(9, **{"kb_knowledge_base.active": "false"}),
    ]})
    store = _wire(servicenow_connector, fake)

    await servicenow_connector._sync_articles()

    assert store.keys() == ["KB0000001", "KB0000002"]


# --- SN-18: one record per number ------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_versions_one_record_with_published_content(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    fake = FakeServiceNow({"kb_knowledge": [
        _article(1, EARLIER, sys_id=V1, workflow_state="outdated", text="<p>v1</p>"),
        _article(1, LATER, sys_id=V2, text="<p>v2</p>"),
    ]})
    store = _wire(sc, fake)

    await sc._sync_articles()

    assert store.keys() == [KB1]
    assert store.records[KB1].external_revision_id == LATER
    assert await sc._fetch_article_content(KB1) == "<p>v2</p>"


@pytest.mark.asyncio
async def test_new_version_narrowing_criteria_leaves_no_old_grants(servicenow_connector) -> None:  # noqa: F811
    # v1 goes outdated without its sys_updated_on changing: only v2 is in the delta.
    sc = servicenow_connector
    fake = FakeServiceNow({
        "kb_knowledge": [
            _article(1, EARLIER, sys_id=V1, workflow_state="outdated", can_read_user_criteria="wide"),
            _article(1, LATER, sys_id=V2, can_read_user_criteria="narrow"),
        ],
        "user_criteria": [{"sys_id": "wide", "user": "u1,u2", "sys_updated_on": T0},
                          {"sys_id": "narrow", "user": "u1", "sys_updated_on": T0}],
    })
    store = _wire(sc, fake, stored_record(KB1), sync_point=migrated_sync_point(T0))
    _users_by_source_id(sc)

    await sc._sync_articles()

    assert store.keys() == [KB1]  # no second record for the old version keeps its grants
    assert sorted(p.email for p in store.permissions[KB1]) == ["u1@x.com"]


@pytest.mark.asyncio
async def test_draft_of_next_version_keeps_published_record(servicenow_connector) -> None:  # noqa: F811
    # During an edit the draft is the latest row; the published one is still the article.
    sc = servicenow_connector
    fake = FakeServiceNow({"kb_knowledge": [
        _article(1, EARLIER, sys_id=V1, latest="false"),
        _article(1, LATER, sys_id=V2, workflow_state="draft", latest="true"),
    ]})
    store = _wire(sc, fake, stored_record(KB1), sync_point=migrated_sync_point(T0))

    await sc._sync_articles()

    assert store.deleted == []
    assert store.records[KB1].external_revision_id == EARLIER  # written from the published row
    assert sc.article_sync_point.data["articles"]["last_sync_time"] == LATER


def test_weburl_uses_number(servicenow_connector) -> None:  # noqa: F811
    servicenow_connector.instance_url = INSTANCE
    record = servicenow_connector._transform_to_article_webpage_record(_row())

    assert record.weburl == f"{INSTANCE}/kb?id=kb_article_view&sysparm_article={KB1}"
    assert record.external_record_id == KB1


# --- SN-19: retired, unpublished and inactive articles go ------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"workflow_state": "retired"}, {"workflow_state": "pending_retirement"}])
async def test_retired_article_deleted_on_incremental(servicenow_connector, change) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [_article(1, LATER, **change)]})
    store = _wire(servicenow_connector, fake, stored_record(KB1), stored_record("f1", "FILE", parent=KB1),
                  sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert store.keys() == []  # with its attachment
    assert servicenow_connector.article_sync_point.data["articles"]["last_sync_time"] == LATER


@pytest.mark.asyncio
async def test_inactive_article_deleted(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [_article(1, LATER, active="false")]})
    store = _wire(servicenow_connector, fake, stored_record(KB1), sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert store.deleted == [KB1]


@pytest.mark.asyncio
async def test_draft_of_a_new_article_deletes_nothing(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [_article(2, LATER, workflow_state="draft")]})
    store = _wire(servicenow_connector, fake, stored_record(KB1), sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]
    servicenow_connector.data_entities_processor.delete_record_by_external_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_retired_article_is_404(servicenow_connector) -> None:  # noqa: F811
    _wire(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1, workflow_state="retired")]}))

    with pytest.raises(HTTPException) as raised:
        await servicenow_connector._fetch_article_content(KB1)

    assert raised.value.status_code == 404


# --- SN-20: the reconcile ----------------------------------------------------------------------


def _stale_reconcile(checkpoint: str | None = None) -> MemorySyncPoint:
    return migrated_sync_point(checkpoint, **{"articles_reconcile/last": {"last_sync_time": "2024-01-01 00:00:00"}})


@pytest.mark.asyncio
async def test_reconcile_deletes_missing_article(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [_article(1)]})
    store = _wire(servicenow_connector, fake, stored_record(KB1), stored_record("KB0000002"),
                  stored_record("f2", "FILE", parent="KB0000002"), sync_point=_stale_reconcile(T0))

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]
    reconciled = servicenow_connector.article_sync_point.data["articles_reconcile/last"]["last_sync_time"]
    assert reconciled > "2024-01-01 00:00:00"


@pytest.mark.asyncio
async def test_reconcile_waits_a_day(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [_article(1)]})
    store = _wire(servicenow_connector, fake, stored_record("KB0000002"), sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert "KB0000002" in store.records
    assert [limit for t, _, limit in fake.calls if t == "kb_knowledge"] == [100, 100, 100]  # delta, resolve, Cannot Read


@pytest.mark.asyncio
async def test_reconcile_skipped_after_partial_listing(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [_article(i) for i in range(1, 1500)]})
    # page 2 of the reconcile's listing (1,000 ids a page) fails
    fake.fail = lambda table, q: status_error(503) if table == "kb_knowledge" and "^NQ" in q and \
        fake.calls[-1][2] == 1000 else None
    store = _wire(servicenow_connector, fake, stored_record("KB9999999"), sync_point=_stale_reconcile())

    await servicenow_connector._sync_articles()

    assert "KB9999999" in store.records
    assert servicenow_connector.article_sync_point.data["articles_reconcile/last"]["last_sync_time"] == "2024-01-01 00:00:00"


@pytest.mark.asyncio
async def test_reconcile_skipped_on_empty_listing(servicenow_connector) -> None:  # noqa: F811
    store = _wire(servicenow_connector, FakeServiceNow({"kb_knowledge": []}), stored_record(KB1),
                  sync_point=_stale_reconcile())

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]


@pytest.mark.asyncio
async def test_failed_article_does_not_block_the_reconcile(servicenow_connector) -> None:  # noqa: F811
    # One article failing every run must not keep deleted ones searchable for good.
    fake = FakeServiceNow({"kb_knowledge": [_article(1)]},
                          fail=lambda table, q: status_error(503) if table == "sys_attachment" else None)
    store = _wire(servicenow_connector, fake, stored_record(KB1), stored_record("KB0000002"),
                  sync_point=_stale_reconcile())

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]  # the failed article keeps its stored record


# --- SN-21: expiry ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_article_deleted(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [_article(1, LATER, valid_to="2024-01-01")]})
    store = _wire(servicenow_connector, fake, stored_record(KB1), sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert store.deleted == [KB1]


@pytest.mark.asyncio
async def test_article_expiring_without_update_deleted_next_day(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [
        _article(1, EARLIER, valid_to=_today(-1)),  # passed since the last check, its row unchanged
        _article(2, EARLIER, valid_to=_today()),
    ]})
    sync_point = migrated_sync_point(T0, articles_expiry={"last_sync_time": _today(-1)})
    store = _wire(servicenow_connector, fake, stored_record(KB1), stored_record("KB0000002"), sync_point=sync_point)

    await servicenow_connector._sync_articles()

    assert store.keys() == ["KB0000002"]
    assert sync_point.data["articles_expiry"] == {"last_sync_time": _today()}


# --- SN-22: attachments and inline images ----------------------------------------------------


def _inline_image(n: int, article: str, updated: str) -> dict:
    return {**_attachment(n, article), "table_name": "ZZ_YYkb_knowledge", "sys_updated_on": updated}


@pytest.mark.asyncio
async def test_inline_image_added_without_article_edit_reprocesses_article(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({
        "kb_knowledge": [_article(1, EARLIER), _article(2, EARLIER)],
        "sys_attachment": [_inline_image(9, "a0001", LATER)],
    })
    sync_point = migrated_sync_point(T0, attachments={"last_sync_time": T0})
    store = _wire(servicenow_connector, fake, sync_point=sync_point)

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]  # not KB0000002: nothing of it changed
    assert sync_point.data["attachments"] == {"last_sync_time": LATER}
    # inline images are not file attachments of the article
    assert not any(r.record_type == "FILE" for r in store.records.values())


@pytest.mark.asyncio
async def test_removed_attachment_deleted(servicenow_connector) -> None:  # noqa: F811
    # Removing a file attachment updates the article row, so the delta has it.
    fake = FakeServiceNow({"kb_knowledge": [_article(1, LATER)], "sys_attachment": [_attachment(2, "a0001")]})
    store = _wire(servicenow_connector, fake, stored_record(KB1), stored_record("f0001", "FILE", parent=KB1),
                  sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1, "f0002"]


@pytest.mark.asyncio
async def test_no_prune_after_failed_listing(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [_article(1, LATER)]},
                          fail=lambda table, q: status_error(503) if table == "sys_attachment" else None)
    store = _wire(servicenow_connector, fake, stored_record(KB1), stored_record("f0001", "FILE", parent=KB1),
                  sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1, "f0001"]
    assert servicenow_connector.full_sync_coverage().complete is False


# --- SN-23: knowledge bases and categories that are gone or inactive -------------------------


@pytest.mark.asyncio
async def test_deleted_kb_removed_with_children(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb1", "kb2"]}})
    store = _wire(sc, FakeServiceNow({"kb_knowledge_base": [_kb("kb1")]}),
                  stored_record(KB1, group="kb2"))

    await sc._sync_knowledge_bases([])

    assert store.deleted_groups == ["kb2"]
    assert store.keys() == []
    assert sc.category_sync_point.data["kb_scope"] == {"ids": ["kb1"]}


@pytest.mark.asyncio
async def test_inactive_kb_removed(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb1", "kb2"]}})
    store = _wire(sc, FakeServiceNow({"kb_knowledge_base": [_kb("kb1"), _kb("kb2", active="false")]}))

    await sc._sync_knowledge_bases([])

    assert store.deleted_groups == ["kb2"]
    assert _groups_written(sc) == ["kb1"]
    assert sc._live_kb_ids == {"kb1"}


@pytest.mark.asyncio
async def test_inactive_kb_stored_before_the_first_listing_removed(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector  # no stored scope yet: the first run after the upgrade
    store = _wire(sc, FakeServiceNow({"kb_knowledge_base": [_kb("kb1"), _kb("kb2", active="false")]}))

    await sc._sync_knowledge_bases([])

    assert store.deleted_groups == ["kb2"]


@pytest.mark.asyncio
async def test_no_removal_after_partial_kb_walk(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb-gone"]}})
    fake = FakeServiceNow({"kb_knowledge_base": [_kb(f"kb{i:03d}") for i in range(150)]},
                          fail=lambda table, q: status_error(503) if "^NQ" in (q or "") else None)
    store = _wire(sc, fake)

    with pytest.raises(Exception, match="503|error"):
        await sc._sync_knowledge_bases([])

    assert store.deleted_groups == []
    assert sc.category_sync_point.data["kb_scope"] == {"ids": ["kb-gone"]}


@pytest.mark.asyncio
async def test_empty_kb_listing_removes_no_category(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb1"]}, "category_scope": {"ids": ["c1"]}})
    store = _wire(sc, FakeServiceNow({"kb_knowledge_base": [], "kb_category": [_category("c1", "kb1")]}))

    await sc._sync_knowledge_bases([])
    await sc._sync_categories()

    assert store.deleted_groups == []
    assert _groups_written(sc) == ["c1"]


@pytest.mark.asyncio
async def test_deleted_category_removed(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"category_scope": {"ids": ["c1", "c2"]}})
    sc._live_kb_ids = {"kb1"}
    store = _wire(sc, FakeServiceNow({"kb_category": [_category("c1", "kb1")]}))

    await sc._sync_categories()

    assert store.deleted_groups == ["c2"]
    assert sc.category_sync_point.data["category_scope"] == {"ids": ["c1"]}


@pytest.mark.asyncio
async def test_categories_of_an_inactive_kb_are_not_written(servicenow_connector) -> None:  # noqa: F811
    # Written, their parents would come back as stub groups named by their sys_id.
    sc = servicenow_connector
    sc._live_kb_ids = {"kb1"}
    _wire(sc, FakeServiceNow({"kb_category": [
        _category("c1", "kb1"), _category("c1a", "c1", active="false"),  # an inactive category stays
        _category("c2", "kb2"), _category("c2a", "c2"),
    ]}))

    await sc._sync_categories()

    assert _groups_written(sc) == ["c1", "c1a"]
    assert sc._category_ids == {"c1", "c1a"}


@pytest.mark.asyncio
async def test_kb_back_in_scope_rereads_its_categories_and_articles(servicenow_connector) -> None:  # noqa: F811
    # Its categories and articles were removed with it and have not changed since.
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb1"]}, "category_scope": {"ids": ["c1"]},
                                              "categories": {"last_sync_time": T0}})
    fake = FakeServiceNow({
        "kb_knowledge_base": [_kb("kb1"), _kb("kb2")],
        "kb_category": [_category("c1", "kb1"), _category("c2", "kb2")],
        "kb_knowledge": [_article(1, EARLIER), _article(2, EARLIER, kb_knowledge_base="kb2", kb_category="c2")],
    })
    store = _wire(sc, fake, sync_point=migrated_sync_point(T0))

    await sc._sync_knowledge_bases([])
    await sc._sync_categories()
    await sc._sync_articles()

    assert sc._kbs_back_in_scope == {"kb2"}
    assert _groups_written(sc) == ["c1", "c2", "kb1", "kb2"]
    assert store.keys() == ["KB0000002"]


# --- SN-24: untitled articles and knowledge bases ---------------------------------------------


def test_untitled_article_named_by_number(servicenow_connector) -> None:  # noqa: F811
    record = servicenow_connector._transform_to_article_webpage_record(_row(short_description=""))

    assert record.record_name == KB1


def test_untitled_kb_still_written(servicenow_connector) -> None:  # noqa: F811
    group = servicenow_connector._transform_to_kb_record_group(TableAPIRecord(**_kb("kb1", title="")))

    assert (group.external_group_id, group.name) == ("kb1", "kb1")


# --- SN-25: placeholders and stub groups ------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_placeholder_is_404(servicenow_connector) -> None:  # noqa: F811
    stub = SimpleNamespace(**vars(stored_record(KB1)), record_name=KB1)
    stub.is_placeholder = True
    servicenow_connector._fetch_article_content = AsyncMock(side_effect=AssertionError("a stub is not fetched"))

    with pytest.raises(HTTPException) as raised:
        await servicenow_connector.stream_record(stub)

    assert raised.value.status_code == 404


def test_article_in_an_unsynced_category_hangs_under_its_kb(servicenow_connector) -> None:  # noqa: F811
    # Under a stub group named by the category's sys_id the article would reach nobody.
    servicenow_connector._category_ids = {"cat1"}
    record = servicenow_connector._transform_to_article_webpage_record(
        _row(kb_category="hidden"))

    assert (record.external_record_group_id, record.record_group_type) == ("kb1", RecordGroupType.SERVICENOWKB)


# --- SN-26: the attachment link --------------------------------------------------------------


def test_attachment_weburl_is_article_url(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    sc.instance_url = INSTANCE
    article = sc._transform_to_article_webpage_record(_row())

    record = sc._transform_to_attachment_file_record(AttachmentMetadata(**_attachment(1, "a0001")), article=article)

    assert record.weburl == article.weburl
    assert record.parent_external_record_id == KB1


# --- SN-70: sys_audit_delete, for an admin account -------------------------------------------


def _audited(display_value: str) -> dict:
    return {"sys_id": "d1", "tablename": "kb_knowledge", "documentkey": V1, "display_value": display_value,
            "sys_updated_on": LATER}


@pytest.mark.asyncio
async def test_audit_delete_of_published_article_deletes_record(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [], "sys_audit_delete": [_audited(f"{KB1} v1.0")]})
    sync_point = migrated_sync_point(T0, audit_delete={"last_sync_time": T0})
    store = _wire(servicenow_connector, fake, stored_record(KB1), sync_point=sync_point)

    await servicenow_connector._sync_articles()

    assert store.deleted == [KB1]
    assert sync_point.data["audit_delete"] == {"last_sync_time": LATER}


@pytest.mark.asyncio
async def test_audit_delete_of_old_version_keeps_record(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [_article(1, EARLIER, sys_id=V2)],
                           "sys_audit_delete": [_audited(f"{KB1} v1.0")]})
    store = _wire(servicenow_connector, fake, stored_record(KB1),
                  sync_point=migrated_sync_point(T0, audit_delete={"last_sync_time": T0}))

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]


@pytest.mark.asyncio
async def test_audit_delete_403_falls_back_to_reconcile(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [], "sys_audit_delete": [_audited(f"{KB1} v1.0")]},
                          fail=lambda table, q: status_error(403) if table == "sys_audit_delete" else None)
    sync_point = migrated_sync_point(T0, audit_delete={"last_sync_time": T0})
    store = _wire(servicenow_connector, fake, stored_record(KB1), sync_point=sync_point)

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]  # until the reconcile
    assert sync_point.data["audit_delete"] == {"last_sync_time": T0}
    assert servicenow_connector.full_sync_coverage().complete is True


# --- Migration: records stored under a version's sys_id --------------------------------------


def _installed_before_the_upgrade(fail_criteria: bool = False) -> tuple[FakeServiceNow, tuple]:
    """KB0000001 had v1 (one file of its own) and has v2 now (one file, copied at checkout). Both
    versions were stored under their sys_id; the install has an article checkpoint and no marker."""
    fake = FakeServiceNow(
        {
            "kb_knowledge": [
                _article(1, EARLIER, sys_id=V1, workflow_state="outdated"),
                _article(1, EARLIER, sys_id=V2, can_read_user_criteria="uc1"),
            ],
            "sys_attachment": [_attachment(2, V2)],
            "user_criteria": [{"sys_id": "uc1", "user": "u1", "sys_updated_on": T0}],
        },
        fail=lambda table, q: status_error(503) if fail_criteria and table == "user_criteria" else None,
    )
    stored = (
        stored_record(V1), stored_record("f0001", "FILE", parent=V1),  # v1 only: its checkout made new copies
        stored_record(V2), stored_record("f0002", "FILE", parent=V2),
    )
    return fake, stored


@pytest.mark.asyncio
async def test_migration_replaces_sys_id_records_with_number_record(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    fake, stored = _installed_before_the_upgrade()
    store = _wire(sc, fake, *stored, sync_point=MemorySyncPoint({"articles": {"last_sync_time": T0}}))
    _users_by_source_id(sc)

    await sc._sync_articles()

    assert "sys_updated_on>" not in fake.calls_to("kb_knowledge")[0]  # read in full, checkpoint or not
    assert store.keys("WEBPAGE") == [KB1]
    assert sc.article_sync_point.data["article_key/v2"] == {"done": True}


@pytest.mark.asyncio
async def test_migration_keeps_current_version_attachments(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    fake, stored = _installed_before_the_upgrade()
    store = _wire(sc, fake, *stored, sync_point=MemorySyncPoint({"articles": {"last_sync_time": T0}}))
    _users_by_source_id(sc)

    await sc._sync_articles()

    assert store.keys("FILE") == ["f0002"]  # moved under the number; v1's own file went with v1
    assert store.records["f0002"].parent_external_record_id == KB1
    assert store.records["f0002"].id == "id-f0002"  # the same record, so no re-index of the file


@pytest.mark.asyncio
async def test_migration_keeps_the_record_of_an_article_that_failed(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    fake, stored = _installed_before_the_upgrade(fail_criteria=True)
    store = _wire(sc, fake, *stored, sync_point=MemorySyncPoint({"articles": {"last_sync_time": T0}}))

    await sc._sync_articles()

    # v2 was not rewritten, so its record still holds its file; v1 is an old version and goes
    assert store.keys() == sorted([V2, "f0002"])
    assert sc.article_sync_point.data["articles_reconcile/last"]["kept"] == [V2]
    assert sc.article_sync_point.data["article_key/v2"] == {"done": True}  # no full read every run

    fixed, _ = _installed_before_the_upgrade()
    sc._get_fresh_datasource = AsyncMock(return_value=fixed)
    _users_by_source_id(sc)
    await sc._sync_articles()

    assert "sys_updated_on>" in fixed.calls_to("kb_knowledge")[0]  # the checkpoint held before v2
    assert store.keys() == [KB1, "f0002"]  # reconciled again at once, not a day later
    assert store.records["f0002"].parent_external_record_id == KB1
    assert sc.article_sync_point.data["articles_reconcile/last"]["kept"] == []


@pytest.mark.asyncio
async def test_migration_repeats_after_a_failed_listing(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    fake, stored = _installed_before_the_upgrade()
    # the reconcile's listing (1,000 ids a page) fails
    fake.fail = lambda table, q: status_error(503) if table == "kb_knowledge" and fake.calls[-1][2] == 1000 else None
    store = _wire(sc, fake, *stored, sync_point=MemorySyncPoint({"articles": {"last_sync_time": T0}}))
    _users_by_source_id(sc)

    await sc._sync_articles()

    assert set(store.keys("WEBPAGE")) == {KB1, V1, V2}  # nothing deleted
    assert "article_key/v2" not in sc.article_sync_point.data

    fake.fail = None
    fake.calls.clear()
    await sc._sync_articles()

    assert "sys_updated_on>" not in fake.calls_to("kb_knowledge")[0]  # read in full again
    assert store.keys() == [KB1, "f0002"]
    assert sc.article_sync_point.data["article_key/v2"] == {"done": True}


@pytest.mark.asyncio
async def test_second_run_after_migration_is_incremental(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    fake, stored = _installed_before_the_upgrade()
    _wire(sc, fake, *stored, sync_point=MemorySyncPoint({"articles": {"last_sync_time": T0}}))
    _users_by_source_id(sc)
    await sc._sync_articles()

    fake.calls.clear()
    await sc._sync_articles()

    assert "sys_updated_on>" in fake.calls_to("kb_knowledge")[0]
    assert all(limit == 100 for t, _, limit in fake.calls if t == "kb_knowledge")  # and no reconcile


@pytest.mark.asyncio
async def test_record_stored_under_a_sys_id_still_streams_until_migrated(servicenow_connector) -> None:  # noqa: F811
    _wire(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1, sys_id=V2, text="<p>v2</p>")]}))

    assert await servicenow_connector._fetch_article_content(V2) == "<p>v2</p>"


def test_kb_knowledge_model_carries_number(servicenow_connector) -> None:  # noqa: F811
    servicenow_connector.instance_url = INSTANCE
    record = servicenow_connector._transform_to_article_webpage_record(
        KBKnowledge(sys_id=V2, number=KB1, short_description="t", kb_knowledge_base="kb1"))

    assert record.external_record_id == KB1
