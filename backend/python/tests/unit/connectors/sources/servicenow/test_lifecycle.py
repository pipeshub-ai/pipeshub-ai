"""ServiceNow record identity and lifecycle: one record per article number, following its current version;
what ServiceNow search does not show is not indexed; deletes, expiry, removed attachments, gone knowledge
bases and categories; and the one-time move of records stored under a version's sys_id.

The Table API is ``FakeServiceNow``; the stored records are ``FakeRecordStore``.
"""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from servicenow_fakes import (
    EARLIER,
    FROZEN_NOW,
    INSTANCE,
    KB1,
    LATER,
    T0,
    V1,
    V2,
    FakeServiceNow,
    MemorySyncPoint,
    article_row,
    attachment_row,
    block_groups,
    category_row,
    file_key,
    groups_written,
    kb_row,
    migrated_sync_point,
    status_error,
    stored_record,
    wire_store,
)

from app.models.entities import RecordGroupType
from app.sources.client.servicenow.servicenow import ServiceNowRateLimited
from app.sources.external.servicenow.models import (
    AttachmentMetadata,
    KBKnowledge,
    TableAPIRecord,
)


def _today(days: int = 0) -> str:
    """A date relative to the frozen clock: tests that use it take the frozen_now fixture."""
    return (FROZEN_NOW + timedelta(days=days)).strftime("%Y-%m-%d")


class _EveryUser(dict):
    """Every source id is a synced app user, with the email <id>@x.com."""

    def __contains__(self, key: object) -> bool:
        return True

    def __missing__(self, key: str) -> str:
        return f"{key}@x.com"


def _users_by_source_id(sc) -> None:
    sc._email_by_source_id = _EveryUser()


def _row(n: int = 1, **fields: str) -> TableAPIRecord:
    """An article row with every field the sync reads."""
    return TableAPIRecord(**{"author": "", "can_read_user_criteria": "", "cannot_read_user_criteria": "",
                             **article_row(n, **fields)})


# --- the rule: only the rows ServiceNow search shows ---------------------------------------------


@pytest.mark.asyncio
async def test_only_rows_search_shows_are_indexed(servicenow_connector, frozen_now) -> None:
    fake = FakeServiceNow({"kb_knowledge": [
        article_row(1),
        article_row(2, valid_to=_today()),  # expires at the end of today
        article_row(3, workflow_state="retired"),
        article_row(4, workflow_state="outdated"),
        article_row(5, workflow_state="draft"),
        article_row(6, workflow_state="review"),
        article_row(7, active="false"),
        article_row(8, valid_to=_today(-1)),
        article_row(9, **{"kb_knowledge_base.active": "false"}),
    ]})
    store = wire_store(servicenow_connector, fake)

    await servicenow_connector._sync_articles()

    assert store.keys() == ["KB0000001", "KB0000002"]


# --- one record per number -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_versions_one_record_with_published_content(servicenow_connector) -> None:
    sc = servicenow_connector
    fake = FakeServiceNow({"kb_knowledge": [
        article_row(1, EARLIER, sys_id=V1, workflow_state="outdated", text="<p>v1</p>"),
        article_row(1, LATER, sys_id=V2, text="<p>v2</p>"),
    ]})
    store = wire_store(sc, fake)

    await sc._sync_articles()

    assert store.keys() == [KB1]
    assert store.records[KB1].external_revision_id == sc._content_revision("<p>v2</p>", [])
    assert block_groups(await sc._fetch_article_content(KB1))[0]["data"].endswith("v2")


@pytest.mark.asyncio
async def test_new_version_narrowing_criteria_leaves_no_old_grants(servicenow_connector) -> None:
    # v1 goes outdated without its sys_updated_on changing: only v2 is in the delta.
    sc = servicenow_connector
    fake = FakeServiceNow({
        "kb_knowledge": [
            article_row(1, EARLIER, sys_id=V1, workflow_state="outdated", can_read_user_criteria="wide"),
            article_row(1, LATER, sys_id=V2, can_read_user_criteria="narrow"),
        ],
        "user_criteria": [{"sys_id": "wide", "user": "u1,u2", "sys_updated_on": T0},
                          {"sys_id": "narrow", "user": "u1", "sys_updated_on": T0}],
    })
    store = wire_store(sc, fake, stored_record(KB1), sync_point=migrated_sync_point(T0))
    _users_by_source_id(sc)

    await sc._sync_articles()

    assert store.keys() == [KB1]  # no second record for the old version keeps its grants
    assert sorted(p.email for p in store.permissions[KB1]) == ["u1@x.com"]


@pytest.mark.asyncio
async def test_draft_of_next_version_keeps_published_record(servicenow_connector) -> None:
    # During an edit the draft is the latest row; the published one is still the article.
    sc = servicenow_connector
    fake = FakeServiceNow({"kb_knowledge": [
        article_row(1, EARLIER, sys_id=V1, latest="false", text="<p>published</p>"),
        article_row(1, LATER, sys_id=V2, workflow_state="draft", latest="true", text="<p>draft</p>"),
    ]})
    store = wire_store(sc, fake, stored_record(KB1), sync_point=migrated_sync_point(T0))

    await sc._sync_articles()

    assert store.deleted == []
    # written from the published row
    assert store.records[KB1].external_revision_id == sc._content_revision("<p>published</p>", [])
    assert sc.article_sync_point.data["articles"]["last_sync_time"] == LATER


def test_weburl_uses_number(servicenow_connector) -> None:
    servicenow_connector.instance_url = INSTANCE
    record = servicenow_connector._transform_to_article_webpage_record(_row())

    assert record.weburl == f"{INSTANCE}/kb?id=kb_article_view&sysparm_article={KB1}"
    assert record.external_record_id == KB1


# --- retired, unpublished and inactive articles go -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"workflow_state": "retired"}, {"workflow_state": "pending_retirement"}])
async def test_retired_article_deleted_on_incremental(servicenow_connector, change) -> None:
    fake = FakeServiceNow({"kb_knowledge": [article_row(1, LATER, **change)]})
    store = wire_store(servicenow_connector, fake, stored_record(KB1), stored_record("f1", "FILE", parent=KB1),
                  sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert store.keys() == []  # with its attachment
    assert servicenow_connector.article_sync_point.data["articles"]["last_sync_time"] == LATER


@pytest.mark.asyncio
async def test_inactive_article_deleted(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": [article_row(1, LATER, active="false")]})
    store = wire_store(servicenow_connector, fake, stored_record(KB1), sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert store.deleted == [KB1]


@pytest.mark.asyncio
async def test_draft_of_a_new_article_deletes_nothing(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": [article_row(2, LATER, workflow_state="draft")]})
    store = wire_store(servicenow_connector, fake, stored_record(KB1), sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]
    servicenow_connector.data_entities_processor.delete_record_by_external_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_retired_article_is_404(servicenow_connector) -> None:
    wire_store(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1, workflow_state="retired")]}))

    with pytest.raises(HTTPException) as raised:
        await servicenow_connector._fetch_article_content(KB1)

    assert raised.value.status_code == 404


# --- the reconcile -----------------------------------------------------------------------------


def _stale_reconcile(checkpoint: str | None = None) -> MemorySyncPoint:
    return migrated_sync_point(checkpoint, **{"articles_reconcile/last": {"last_sync_time": "2024-01-01 00:00:00"}})


@pytest.mark.asyncio
async def test_reconcile_deletes_missing_article(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": [article_row(1)]})
    store = wire_store(servicenow_connector, fake, stored_record(KB1), stored_record("KB0000002"),
                  stored_record("f2", "FILE", parent="KB0000002"), sync_point=_stale_reconcile(T0))

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]
    reconciled = servicenow_connector.article_sync_point.data["articles_reconcile/last"]["last_sync_time"]
    assert reconciled > "2024-01-01 00:00:00"


@pytest.mark.asyncio
async def test_reconcile_waits_a_day(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": [article_row(1)]})
    store = wire_store(servicenow_connector, fake, stored_record("KB0000002"), sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert "KB0000002" in store.records
    assert [limit for t, _, limit in fake.calls if t == "kb_knowledge"] == [100, 100, 100]  # delta, resolve, Cannot Read


@pytest.mark.asyncio
async def test_reconcile_skipped_after_partial_listing(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": [article_row(i) for i in range(1, 1500)]})
    # page 2 of the reconcile's listing (1,000 ids a page) fails
    fake.fail = lambda table, q: status_error(503) if table == "kb_knowledge" and "^NQ" in q and \
        fake.calls[-1][2] == 1000 else None
    store = wire_store(servicenow_connector, fake, stored_record("KB9999999"), sync_point=_stale_reconcile())

    await servicenow_connector._sync_articles()

    assert "KB9999999" in store.records
    assert servicenow_connector.article_sync_point.data["articles_reconcile/last"]["last_sync_time"] == "2024-01-01 00:00:00"


@pytest.mark.asyncio
async def test_reconcile_skipped_on_empty_listing(servicenow_connector) -> None:
    store = wire_store(servicenow_connector, FakeServiceNow({"kb_knowledge": []}), stored_record(KB1),
                  sync_point=_stale_reconcile())

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]


@pytest.mark.asyncio
async def test_failed_article_does_not_block_the_reconcile(servicenow_connector) -> None:
    # One article failing every run must not keep deleted ones searchable for good.
    fake = FakeServiceNow({"kb_knowledge": [article_row(1)]},
                          fail=lambda table, q: status_error(503) if table == "sys_attachment" else None)
    store = wire_store(servicenow_connector, fake, stored_record(KB1), stored_record("KB0000002"),
                  sync_point=_stale_reconcile())

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]  # the failed article keeps its stored record


# --- expiry ----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_expired_article_deleted(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": [article_row(1, LATER, valid_to="2024-01-01")]})
    store = wire_store(servicenow_connector, fake, stored_record(KB1), sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert store.deleted == [KB1]


@pytest.mark.asyncio
async def test_article_expiring_without_update_deleted_next_day(servicenow_connector, frozen_now) -> None:
    fake = FakeServiceNow({"kb_knowledge": [
        article_row(1, EARLIER, valid_to=_today(-1)),  # passed since the last check, its row unchanged
        article_row(2, EARLIER, valid_to=_today()),
    ]})
    sync_point = migrated_sync_point(T0, articles_expiry={"last_sync_time": _today(-1)})
    store = wire_store(servicenow_connector, fake, stored_record(KB1), stored_record("KB0000002"), sync_point=sync_point)

    await servicenow_connector._sync_articles()

    assert store.keys() == ["KB0000002"]
    assert sync_point.data["articles_expiry"] == {"last_sync_time": _today()}


# --- attachments and inline images -----------------------------------------------------------


def _inline_image(n: int, article: str, updated: str) -> dict:
    return {**attachment_row(n, article), "table_name": "ZZ_YYkb_knowledge", "sys_updated_on": updated}


@pytest.mark.asyncio
async def test_inline_image_added_without_article_edit_reprocesses_article(servicenow_connector) -> None:
    fake = FakeServiceNow({
        "kb_knowledge": [article_row(1, EARLIER), article_row(2, EARLIER)],
        "sys_attachment": [_inline_image(9, "a0001", LATER)],
    })
    sync_point = migrated_sync_point(T0, attachments={"last_sync_time": T0})
    store = wire_store(servicenow_connector, fake, sync_point=sync_point)

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]  # not KB0000002: nothing of it changed
    assert sync_point.data["attachments"] == {"last_sync_time": LATER,
                                              "synced_in_overlap": [f"{LATER}|{_inline_image(9, 'a0001', LATER)['sys_id']}"]}
    # inline images are not file attachments of the article
    assert not any(r.record_type == "FILE" for r in store.records.values())


@pytest.mark.asyncio
async def test_removed_attachment_deleted(servicenow_connector) -> None:
    # Removing a file attachment updates the article row, so the delta has it.
    fake = FakeServiceNow({"kb_knowledge": [article_row(1, LATER)], "sys_attachment": [attachment_row(2, "a0001")]})
    store = wire_store(servicenow_connector, fake, stored_record(KB1), stored_record("f0001", "FILE", parent=KB1),
                  sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1, file_key(2)]


@pytest.mark.asyncio
async def test_no_prune_after_failed_listing(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": [article_row(1, LATER)]},
                          fail=lambda table, q: status_error(503) if table == "sys_attachment" else None)
    store = wire_store(servicenow_connector, fake, stored_record(KB1), stored_record("f0001", "FILE", parent=KB1),
                  sync_point=migrated_sync_point(T0))

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1, "f0001"]
    assert servicenow_connector.full_sync_coverage().complete is False


# --- knowledge bases and categories that are gone or inactive --------------------------------


@pytest.mark.asyncio
async def test_deleted_kb_removed_with_children(servicenow_connector) -> None:
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb1", "kb2"]}})
    store = wire_store(sc, FakeServiceNow({"kb_knowledge_base": [kb_row("kb1")]}),
                  stored_record(KB1, group="kb2"))

    await sc._sync_knowledge_bases([])

    assert store.deleted_groups == ["kb2"]
    assert store.keys() == []
    assert sc._scopes_to_store == {"kb_scope": ["kb1"]}  # stored once the articles are synced


@pytest.mark.asyncio
async def test_inactive_kb_removed(servicenow_connector) -> None:
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb1", "kb2"]}})
    store = wire_store(sc, FakeServiceNow({"kb_knowledge_base": [kb_row("kb1"), kb_row("kb2", active="false")]}))

    await sc._sync_knowledge_bases([])

    assert store.deleted_groups == ["kb2"]
    assert groups_written(sc) == ["kb1"]
    assert sc._live_kb_ids == {"kb1"}


@pytest.mark.asyncio
async def test_inactive_kb_stored_before_the_first_listing_removed(servicenow_connector) -> None:
    sc = servicenow_connector  # no stored scope yet: the first run after the upgrade
    store = wire_store(sc, FakeServiceNow({"kb_knowledge_base": [kb_row("kb1"), kb_row("kb2", active="false")]}))

    await sc._sync_knowledge_bases([])

    assert store.deleted_groups == ["kb2"]


@pytest.mark.asyncio
async def test_no_removal_after_partial_kb_walk(servicenow_connector) -> None:
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb-gone"]}})
    fake = FakeServiceNow({"kb_knowledge_base": [kb_row(f"kb{i:03d}") for i in range(150)]},
                          fail=lambda table, q: status_error(503) if "^NQ" in (q or "") else None)
    store = wire_store(sc, fake)

    with pytest.raises(Exception, match="503|error"):
        await sc._sync_knowledge_bases([])

    assert store.deleted_groups == []
    assert sc.category_sync_point.data["kb_scope"] == {"ids": ["kb-gone"]}


@pytest.mark.asyncio
async def test_empty_kb_listing_removes_no_category(servicenow_connector) -> None:
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb1"]}, "category_scope": {"ids": ["c1"]}})
    store = wire_store(sc, FakeServiceNow({"kb_knowledge_base": [], "kb_category": [category_row("c1", "kb1")]}))

    await sc._sync_knowledge_bases([])
    await sc._sync_categories()

    assert store.deleted_groups == []
    assert groups_written(sc) == ["c1"]


@pytest.mark.asyncio
async def test_deleted_category_removed(servicenow_connector) -> None:
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"category_scope": {"ids": ["c1", "c2"]}})
    sc._live_kb_ids = {"kb1"}
    store = wire_store(sc, FakeServiceNow({"kb_category": [category_row("c1", "kb1")]}))

    await sc._sync_categories()

    assert store.deleted_groups == ["c2"]
    assert sc._scopes_to_store == {"category_scope": ["c1"]}  # stored once the articles are synced


@pytest.mark.asyncio
async def test_categories_of_an_inactive_kb_are_not_written(servicenow_connector) -> None:
    # Written, their parents would come back as stub groups named by their sys_id.
    sc = servicenow_connector
    sc._live_kb_ids = {"kb1"}
    wire_store(sc, FakeServiceNow({"kb_category": [
        category_row("c1", "kb1"), category_row("c1a", "c1", active="false"),  # an inactive category stays
        category_row("c2", "kb2"), category_row("c2a", "c2"),
    ]}))

    await sc._sync_categories()

    assert groups_written(sc) == ["c1", "c1a"]
    assert sc._category_ids == {"c1", "c1a"}


@pytest.mark.asyncio
async def test_kb_back_in_scope_rereads_its_categories_and_articles(servicenow_connector) -> None:
    # Its categories and articles were removed with it and have not changed since.
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb1"]}, "category_scope": {"ids": ["c1"]},
                                              "categories": {"last_sync_time": T0}})
    fake = FakeServiceNow({
        "kb_knowledge_base": [kb_row("kb1"), kb_row("kb2")],
        "kb_category": [category_row("c1", "kb1"), category_row("c2", "kb2")],
        "kb_knowledge": [article_row(1, EARLIER), article_row(2, EARLIER, kb_knowledge_base="kb2", kb_category="c2")],
    })
    store = wire_store(sc, fake, sync_point=migrated_sync_point(T0))

    await sc._sync_knowledge_bases([])
    await sc._sync_categories()
    await sc._sync_articles()

    assert sc._kbs_back_in_scope == {"kb2"}
    assert groups_written(sc) == ["c1", "c2", "kb1", "kb2"]
    assert store.keys() == ["KB0000002"]


@pytest.mark.asyncio
async def test_kb_back_in_scope_is_read_again_after_a_run_that_stopped(servicenow_connector) -> None:
    # Run N stops before its articles are written: run N+1 still sees kb2 as back in scope.
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb1"]}, "category_scope": {"ids": ["c1"]},
                                              "categories": {"last_sync_time": T0}})
    fake = FakeServiceNow({
        "kb_knowledge_base": [kb_row("kb1"), kb_row("kb2")],
        "kb_category": [category_row("c1", "kb1"), category_row("c2", "kb2")],
        "kb_knowledge": [article_row(1, EARLIER), article_row(2, EARLIER, kb_knowledge_base="kb2", kb_category="c2")],
    }, fail=lambda table, q: ServiceNowRateLimited(0, "rule") if table == "kb_knowledge" else None)
    store = wire_store(sc, fake, sync_point=migrated_sync_point(T0))
    await sc._sync_knowledge_bases([])
    await sc._sync_categories()
    with pytest.raises(ServiceNowRateLimited):
        await sc._sync_articles()

    fake.fail = None
    await sc._sync_knowledge_bases([])
    await sc._sync_categories()
    await sc._sync_articles()

    assert sc._kbs_back_in_scope == {"kb2"}
    assert store.keys() == ["KB0000002"]
    assert sc.category_sync_point.data["kb_scope"] == {"ids": ["kb1", "kb2"]}


@pytest.mark.asyncio
async def test_article_of_a_deleted_category_is_kept_under_its_kb(servicenow_connector) -> None:
    # ServiceNow clears the article's kb_category without changing its row; the category's group took it along.
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"category_scope": {"ids": ["c1", "c2"]}, "categories": {"last_sync_time": T0}})
    sc._live_kb_ids = {"kb1"}
    fake = FakeServiceNow({
        "kb_category": [category_row("c1", "kb1")],
        "kb_knowledge": [article_row(1, EARLIER), article_row(2, EARLIER, kb_category="")],
    })
    store = wire_store(sc, fake, stored_record(KB1, group="c1"), stored_record("KB0000002", group="c2"),
                       sync_point=migrated_sync_point(T0))

    await sc._sync_categories()
    await sc._sync_articles()

    assert store.deleted_groups == ["c2"]
    assert store.records["KB0000002"].external_record_group_id == "kb1"
    assert sc.category_sync_point.data["category_scope"] == {"ids": ["c1"]}


@pytest.mark.asyncio
async def test_unlabelled_category_is_written_under_its_parent(servicenow_connector) -> None:
    sc = servicenow_connector
    sc._live_kb_ids = {"kb1"}
    store = wire_store(sc, FakeServiceNow({"kb_category": [category_row("cat1", "kb1", label="")],
                                           "kb_knowledge": [article_row(1)]}))

    await sc._sync_categories()
    await sc._sync_articles()

    [(group, _)] = sc.data_entities_processor.on_new_record_groups.await_args.args[0]
    assert (group.external_group_id, group.name, group.parent_external_group_id) == ("cat1", "cat1", "kb1")
    assert store.records[KB1].external_record_group_id == "cat1"


@pytest.mark.asyncio
async def test_failed_cleanup_is_reported(servicenow_connector, caplog) -> None:
    sc = servicenow_connector
    store = wire_store(sc, FakeServiceNow({"kb_knowledge": [article_row(1)]}), stored_record("KB0000002"),
                       sync_point=_stale_reconcile())
    sc.data_entities_processor.delete_record_by_external_id = AsyncMock(side_effect=RuntimeError("store down"))

    await sc._sync_articles()
    await sc._report_sync_problems()

    assert "KB0000002" in store.records
    assert ("- Some articles or knowledge bases that ServiceNow no longer shows, or the filters leave out, could "
            "not be removed; this is retried on the next sync") in caplog.text


# --- untitled articles and knowledge bases ----------------------------------------------------


def test_untitled_article_named_by_number(servicenow_connector) -> None:
    record = servicenow_connector._transform_to_article_webpage_record(_row(short_description=""))

    assert record.record_name == KB1


def test_untitled_kb_still_written(servicenow_connector) -> None:
    group = servicenow_connector._transform_to_kb_record_group(TableAPIRecord(**kb_row("kb1", title="")))

    assert (group.external_group_id, group.name) == ("kb1", "kb1")


# --- placeholders and stub groups -------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_placeholder_is_404(servicenow_connector) -> None:
    stub = SimpleNamespace(**vars(stored_record(KB1)), record_name=KB1)
    stub.is_placeholder = True
    servicenow_connector._fetch_article_content = AsyncMock(side_effect=AssertionError("a stub is not fetched"))

    with pytest.raises(HTTPException) as raised:
        await servicenow_connector.stream_record(stub)

    assert raised.value.status_code == 404


def test_article_in_an_unsynced_category_hangs_under_its_kb(servicenow_connector) -> None:
    # Under a stub group named by the category's sys_id the article would reach nobody.
    servicenow_connector._category_ids = {"cat1"}
    record = servicenow_connector._transform_to_article_webpage_record(
        _row(kb_category="hidden"))

    assert (record.external_record_group_id, record.record_group_type) == ("kb1", RecordGroupType.SERVICENOWKB)


# --- the attachment link ---------------------------------------------------------------------


def test_attachment_weburl_is_article_url(servicenow_connector) -> None:
    sc = servicenow_connector
    sc.instance_url = INSTANCE
    article = sc._transform_to_article_webpage_record(_row())

    record = sc._transform_to_attachment_file_record(AttachmentMetadata(**attachment_row(1, "a0001")), article=article)

    assert record.weburl == article.weburl
    assert record.parent_external_record_id == KB1


# --- sys_audit_delete, for an admin account --------------------------------------------------


def _audited(display_value: str) -> dict:
    return {"sys_id": "d1", "tablename": "kb_knowledge", "documentkey": V1, "display_value": display_value,
            "sys_updated_on": LATER}


@pytest.mark.asyncio
async def test_audit_delete_of_published_article_deletes_record(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": [], "sys_audit_delete": [_audited(f"{KB1} v1.0")]})
    sync_point = migrated_sync_point(T0, audit_delete={"last_sync_time": T0})
    store = wire_store(servicenow_connector, fake, stored_record(KB1), sync_point=sync_point)

    await servicenow_connector._sync_articles()

    assert store.deleted == [KB1]
    assert sync_point.data["audit_delete"] == {"last_sync_time": LATER}


@pytest.mark.asyncio
async def test_audit_delete_of_old_version_keeps_record(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": [article_row(1, EARLIER, sys_id=V2)],
                           "sys_audit_delete": [_audited(f"{KB1} v1.0")]})
    store = wire_store(servicenow_connector, fake, stored_record(KB1),
                  sync_point=migrated_sync_point(T0, audit_delete={"last_sync_time": T0}))

    await servicenow_connector._sync_articles()

    assert store.keys() == [KB1]


@pytest.mark.asyncio
async def test_audit_delete_403_falls_back_to_reconcile(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": [], "sys_audit_delete": [_audited(f"{KB1} v1.0")]},
                          fail=lambda table, q: status_error(403) if table == "sys_audit_delete" else None)
    sync_point = migrated_sync_point(T0, audit_delete={"last_sync_time": T0})
    store = wire_store(servicenow_connector, fake, stored_record(KB1), sync_point=sync_point)

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
                article_row(1, EARLIER, sys_id=V1, workflow_state="outdated"),
                article_row(1, EARLIER, sys_id=V2, can_read_user_criteria="uc1"),
            ],
            "sys_attachment": [attachment_row(2, V2)],
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
async def test_migration_replaces_sys_id_records_with_number_record(servicenow_connector) -> None:
    sc = servicenow_connector
    fake, stored = _installed_before_the_upgrade()
    store = wire_store(sc, fake, *stored, sync_point=MemorySyncPoint({"articles": {"last_sync_time": T0}}))
    _users_by_source_id(sc)

    await sc._sync_articles()

    assert "sys_updated_on>" not in fake.calls_to("kb_knowledge")[0]  # read in full, checkpoint or not
    assert store.keys("WEBPAGE") == [KB1]
    assert sc.article_sync_point.data["article_key/v2"] == {"done": True}


@pytest.mark.asyncio
async def test_migration_keeps_current_version_attachments(servicenow_connector) -> None:
    sc = servicenow_connector
    fake, stored = _installed_before_the_upgrade()
    store = wire_store(sc, fake, *stored, sync_point=MemorySyncPoint({"articles": {"last_sync_time": T0}}))
    _users_by_source_id(sc)

    await sc._sync_articles()

    # moved under the number and its key; v1's own file went with v1
    assert store.keys("FILE") == [file_key(2)]
    assert store.records[file_key(2)].parent_external_record_id == KB1
    assert store.records[file_key(2)].id == "id-f0002"  # the same record, so its citations still open


@pytest.mark.asyncio
async def test_migration_keeps_the_record_of_an_article_that_failed(servicenow_connector) -> None:
    sc = servicenow_connector
    fake, stored = _installed_before_the_upgrade(fail_criteria=True)
    store = wire_store(sc, fake, *stored, sync_point=MemorySyncPoint({"articles": {"last_sync_time": T0}}))

    await sc._sync_articles()

    # v2 was not rewritten, so its record still holds its file; v1 is an old version and goes
    assert store.keys() == sorted([V2, "f0002"])
    assert sc.article_sync_point.data["articles_reconcile/last"]["kept"] == [V2]
    # A failed article keeps the attachments unmigrated: the next run reads every article again
    # and re-keys their files in place instead of giving them new records.
    assert "attachment_key/v2" not in sc.article_sync_point.data

    fixed, _ = _installed_before_the_upgrade()
    sc._get_fresh_datasource = AsyncMock(return_value=fixed)
    _users_by_source_id(sc)
    await sc._sync_articles()

    assert "sys_updated_on>" not in fixed.calls_to("kb_knowledge")[0]  # read in full again
    assert store.keys() == [KB1, file_key(2)]  # reconciled again at once, not a day later
    assert store.records[file_key(2)].parent_external_record_id == KB1
    assert sc.article_sync_point.data["articles_reconcile/last"]["kept"] == []
    assert store.records[file_key(2)].id == "id-f0002"  # re-keyed in place, so its citations still open
    assert sc.article_sync_point.data["attachment_key/v2"] == {"done": True}


@pytest.mark.asyncio
async def test_migration_repeats_after_a_failed_listing(servicenow_connector) -> None:
    sc = servicenow_connector
    fake, stored = _installed_before_the_upgrade()
    # the reconcile's listing (1,000 ids a page) fails
    fake.fail = lambda table, q: status_error(503) if table == "kb_knowledge" and fake.calls[-1][2] == 1000 else None
    store = wire_store(sc, fake, *stored, sync_point=MemorySyncPoint({"articles": {"last_sync_time": T0}}))
    _users_by_source_id(sc)

    await sc._sync_articles()

    assert set(store.keys("WEBPAGE")) == {KB1, V1, V2}  # nothing deleted
    assert "article_key/v2" not in sc.article_sync_point.data

    fake.fail = None
    fake.calls.clear()
    await sc._sync_articles()

    assert "sys_updated_on>" not in fake.calls_to("kb_knowledge")[0]  # read in full again
    assert store.keys() == [KB1, file_key(2)]
    assert sc.article_sync_point.data["article_key/v2"] == {"done": True}


@pytest.mark.asyncio
async def test_second_run_after_migration_is_incremental(servicenow_connector) -> None:
    sc = servicenow_connector
    fake, stored = _installed_before_the_upgrade()
    wire_store(sc, fake, *stored, sync_point=MemorySyncPoint({"articles": {"last_sync_time": T0}}))
    _users_by_source_id(sc)
    await sc._sync_articles()

    fake.calls.clear()
    await sc._sync_articles()

    assert "sys_updated_on>" in fake.calls_to("kb_knowledge")[0]
    assert all(limit == 100 for t, _, limit in fake.calls if t == "kb_knowledge")  # and no reconcile


@pytest.mark.asyncio
async def test_record_stored_under_a_sys_id_still_streams_until_migrated(servicenow_connector) -> None:
    wire_store(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1, sys_id=V2, text="<p>v2</p>")]}))

    assert block_groups(await servicenow_connector._fetch_article_content(V2))[0]["data"].endswith("v2")


def test_kb_knowledge_model_carries_number(servicenow_connector) -> None:
    servicenow_connector.instance_url = INSTANCE
    record = servicenow_connector._transform_to_article_webpage_record(
        KBKnowledge(sys_id=V2, number=KB1, short_description="t", kb_knowledge_base="kb1"))

    assert record.external_record_id == KB1


@pytest.mark.asyncio
async def test_delta_keeps_an_article_whose_current_version_is_hidden(servicenow_connector) -> None:
    # An old version changed; the current one is there, but an ACL hides it from this account.
    sc = servicenow_connector
    fake = FakeServiceNow({"kb_knowledge": [
        article_row(1, LATER, sys_id=V1, workflow_state="outdated"),
        {**article_row(1, EARLIER, sys_id=V2), "_acl_hidden": True},
        article_row(2, LATER, workflow_state="retired"),  # no current version at all
    ]})
    store = wire_store(sc, fake, stored_record(KB1), stored_record("KB0000002"), sync_point=migrated_sync_point(T0))

    await sc._sync_articles()

    assert store.deleted == ["KB0000002"]
    assert KB1 in store.keys("WEBPAGE")
