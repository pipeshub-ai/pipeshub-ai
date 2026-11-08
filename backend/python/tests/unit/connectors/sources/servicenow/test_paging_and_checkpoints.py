"""ServiceNow sync reads every row, keeps checkpoints behind failures and reports partial runs.

The connector's listings run against ``FakeServiceNow``, which answers encoded queries the way the
Table API does, including pages that come back short because an ACL hid rows after the page was cut.
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest
from servicenow_fakes import (
    INSTANCE,
    T0,
    FakeServiceNow,
    MemorySyncPoint,
    article_row,
    attachment_row,
    file_key,
    kb_row,
    migrated_sync_point,
    status_error,
    stored_record,
    wire_listing,
    wire_store,
    written_ids,
)

from app.config.constants.arangodb import Connectors, MimeTypes
from app.connectors.sources.servicenow.servicenow.constants import (
    ORGANIZATIONAL_ENTITIES,
    ServiceNowDefaults,
)
from app.models.entities import AppUser
from app.sources.client.servicenow.servicenow import ServiceNowRateLimited
from app.sources.external.servicenow.models import (
    AttachmentMetadata,
    ServiceNowAPIError,
    TableAPIRecord,
    TableAPIResponse,
)

# --- a short page does not end a listing (X-Total-Count does: test_resilience_and_auth_refresh.py)


@pytest.mark.asyncio
async def test_acl_short_page_does_not_end_listing(servicenow_connector) -> None:
    groups = [{"sys_id": f"g{i:03d}", "name": f"G{i}", "sys_updated_on": T0} for i in range(103)]
    for hidden in (10, 50, 90):  # page 1 comes back with 97 of its 100 rows
        groups[hidden]["_acl_hidden"] = True
    fake = FakeServiceNow({"sys_user_group": groups})
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)

    result = await servicenow_connector._fetch_all_groups()

    assert len(result) == 100  # every visible row, page 2 included
    assert {g.sys_id for g in result} == {g["sys_id"] for g in groups if not g.get("_acl_hidden")}


@pytest.mark.asyncio
async def test_listing_that_does_not_advance_raises(servicenow_connector) -> None:
    # An instance that ignores the keyset condition answers every page with the first one.
    same = TableAPIResponse(result=[{"sys_id": "g1", "sys_updated_on": T0}], total_count=2000)
    servicenow_connector._get_fresh_datasource = AsyncMock(
        return_value=MagicMock(get_now_table_tableName=AsyncMock(return_value=same)))

    with pytest.raises(ServiceNowAPIError, match="did not advance"):
        await servicenow_connector._fetch_all_groups()


# --- keyset paging and the delta overlap ----------------------------------------------------


@pytest.mark.asyncio
async def test_keyset_paging_reads_every_tied_row(servicenow_connector) -> None:
    # 250 rows share one timestamp, and the instance returns ties in any order.
    groups = [{"sys_id": f"g{i:03d}", "name": f"G{i}", "sys_updated_on": T0} for i in range(250)]
    fake = FakeServiceNow({"sys_user_group": groups}, shuffle_ties=True)
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)

    result = await servicenow_connector._fetch_all_groups()

    assert sorted(g.sys_id for g in result) == [g["sys_id"] for g in groups]


@pytest.mark.asyncio
async def test_row_updated_during_walk_is_not_skipped(servicenow_connector) -> None:
    groups = [{"sys_id": f"g{i:03d}", "name": f"G{i}", "sys_updated_on": T0} for i in range(150)]

    def edit_a_read_row(fake: FakeServiceNow) -> None:
        # after page 1, a row it returned is updated, which moves it to the end of the order
        groups[0]["sys_updated_on"] = "2024-06-02 00:00:00"

    fake = FakeServiceNow({"sys_user_group": groups}, after_call=edit_a_read_row)
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)

    result = await servicenow_connector._fetch_all_groups()

    assert {g.sys_id for g in result} == {g["sys_id"] for g in groups}  # offsets would skip g100


@pytest.mark.asyncio
async def test_delta_reads_with_overlap_window(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": [
        article_row(1, "2024-05-31 23:57:00"),  # before the overlap window: already synced
        article_row(2, "2024-05-31 23:59:30"),  # committed late, dated just before the checkpoint
        article_row(3, T0),  # the checkpoint's own second
        article_row(4, "2024-06-01 10:00:00"),
    ]})
    wire_listing(servicenow_connector, fake, checkpoint=T0)

    await servicenow_connector._sync_articles()

    assert "sys_updated_on>2024-05-31 23:58:00" in fake.calls_to("kb_knowledge")[0]
    assert sorted(written_ids(servicenow_connector)) == ["KB0000002", "KB0000003", "KB0000004"]
    saved = servicenow_connector.article_sync_point.data["articles"]["last_sync_time"]
    assert saved == "2024-06-01 10:00:00"


@pytest.mark.asyncio
async def test_a_second_edit_in_the_same_second_is_synced(servicenow_connector) -> None:
    sc = servicenow_connector
    row = article_row(3, T0, sys_mod_count="4")
    fake = FakeServiceNow({"kb_knowledge": [row]})
    wire_listing(sc, fake, checkpoint=T0)
    await sc._sync_articles()
    await sc._sync_articles()  # the overlap reads it again, unchanged: skipped
    sc.data_entities_processor.on_new_records.reset_mock()
    await sc._sync_articles()
    assert written_ids(sc) == []

    row.update(sys_mod_count="5", short_description="edited again")  # sys_updated_on keeps its second
    await sc._sync_articles()

    assert written_ids(sc) == ["KB0000003"]


# --- paged single reads and chunked IN lists ------------------------------------------------


@pytest.mark.asyncio
async def test_in_list_is_chunked_under_max_url_length(servicenow_connector) -> None:
    ids = [f"{i:032x}" for i in range(100)]
    fake = FakeServiceNow({"user_criteria": [{"sys_id": i, "sys_updated_on": T0, "user": "u1"} for i in ids]})
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)

    rows = await servicenow_connector._fetch_user_criteria(ids)

    assert sorted(r.sys_id for r in rows) == ids
    assert len(fake.calls) > 2
    fields = "sys_id,active,match_all,advanced,script,user,group,role,department,location,company,sys_updated_on"
    for _, query, limit in fake.calls:
        url = (f"{INSTANCE}/api/now/table/user_criteria?sysparm_query={quote(query, safe='')}"
               f"&sysparm_fields={quote(fields, safe='')}&sysparm_limit={limit}&sysparm_offset=0"
               "&sysparm_display_value=false&sysparm_exclude_reference_link=true")
        assert len(url) < ServiceNowDefaults.MAX_URL_LENGTH


@pytest.mark.asyncio
async def test_admin_read_is_paged(servicenow_connector) -> None:
    fake = FakeServiceNow({"sys_user_has_role": [
        {"sys_id": f"r{i:03d}", "user": f"u{i:03d}", "role.name": "admin", "state": "active", "user.active": "true", "sys_updated_on": T0}
        for i in range(150)
    ]})
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)
    servicenow_connector.data_entities_processor.get_user_by_source_id = AsyncMock(
        side_effect=lambda source_user_id, connector_id: AppUser(
            app_name=Connectors.SERVICENOW, connector_id=connector_id, source_user_id=source_user_id,
            email=f"{source_user_id}@x.com", full_name=source_user_id))

    admins = await servicenow_connector._get_admin_users()

    assert len(admins) == 150
    assert [limit for _, _, limit in fake.calls] == [100, 100]


# --- failures hold the checkpoint and the coverage ------------------------------------------


@pytest.mark.asyncio
async def test_failed_article_makes_full_sync_incomplete(servicenow_connector) -> None:
    fake = FakeServiceNow(
        {"kb_knowledge": [article_row(1)]},
        fail=lambda table, query: status_error(500) if table == "sys_attachment" else None,
    )
    wire_listing(servicenow_connector, fake)

    await servicenow_connector._sync_articles()

    assert servicenow_connector.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_unreadable_kb_criteria_makes_full_sync_incomplete(servicenow_connector) -> None:
    fake = FakeServiceNow(
        {"kb_knowledge_base": [{"sys_id": "kb1", "title": "KB", "sys_updated_on": T0}]},
        fail=lambda table, query: status_error(503) if table == "kb_uc_can_read_mtom" else None,
    )
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)
    servicenow_connector.data_entities_processor.note_kept_record_group = AsyncMock()

    await servicenow_connector._sync_knowledge_bases([])

    assert servicenow_connector.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_clean_run_reports_complete(servicenow_connector) -> None:
    sc = servicenow_connector
    wire_listing(sc, FakeServiceNow({"kb_knowledge": [article_row(1)], "sys_attachment": [attachment_row(1, "a0001")]}))
    sc.servicenow_client = MagicMock()
    sc._sync_read_incomplete = True  # left over from the previous run
    with patch.object(sc, "_register_authenticated_identity", new_callable=AsyncMock), \
         patch.object(sc, "_sync_users_and_groups", new_callable=AsyncMock), \
         patch.object(sc, "_get_admin_users", new_callable=AsyncMock, return_value=[]), \
         patch.object(sc, "_sync_knowledge_bases", new_callable=AsyncMock), \
         patch.object(sc, "_sync_categories", new_callable=AsyncMock):
        await sc.run_sync()

    assert sorted(written_ids(sc)) == ["KB0000001", file_key(1)]
    assert sc.full_sync_coverage().complete is True


def _three_articles_second_fails(fail: bool) -> FakeServiceNow:
    # Attachments are listed per page, so the second article fails on its own Can Read criteria.
    return FakeServiceNow(
        {
            "kb_knowledge": [
                article_row(1, "2024-06-01 01:00:00"),
                article_row(2, "2024-06-01 02:00:00", can_read_user_criteria="uc2"),
                article_row(3, "2024-06-01 03:00:00"),
            ],
            "user_criteria": [{"sys_id": "uc2", "user": "u1", "sys_updated_on": T0}],
        },
        fail=lambda table, query: status_error(503) if fail and table == "user_criteria" else None,
    )


@pytest.mark.asyncio
async def test_checkpoint_held_before_oldest_failed_article(servicenow_connector) -> None:
    wire_listing(servicenow_connector, _three_articles_second_fails(fail=True))

    await servicenow_connector._sync_articles()

    assert sorted(written_ids(servicenow_connector)) == ["KB0000001", "KB0000003"]
    assert servicenow_connector.article_sync_point.data["articles"]["last_sync_time"] == "2024-06-01 01:59:59"


@pytest.mark.asyncio
async def test_failed_article_retried_next_run(servicenow_connector) -> None:
    sc = servicenow_connector
    wire_listing(sc, _three_articles_second_fails(fail=True))
    await sc._sync_articles()
    sync_point = sc.article_sync_point

    sc.data_entities_processor.on_new_records.reset_mock()
    wire_listing(sc, _three_articles_second_fails(fail=False))
    sc.article_sync_point = sync_point
    await sc._sync_articles()

    assert "KB0000002" in written_ids(sc)
    assert sync_point.data["articles"]["last_sync_time"] == "2024-06-01 03:00:00"


@pytest.mark.asyncio
async def test_attachment_listing_503_fails_the_article_not_the_attachments(servicenow_connector) -> None:
    # An incremental lists attachments per page of articles, so a failed listing fails that page.
    fake = FakeServiceNow(
        {
            "kb_knowledge": [article_row(i, f"2024-06-01 00:{i // 100:02d}:00") for i in range(101)],
            "sys_attachment": [attachment_row(1, "a0001"), attachment_row(100, "a0100")],
        },
        fail=lambda table, query: status_error(503) if table == "sys_attachment" and "a0001" in query else None,
    )
    wire_listing(servicenow_connector, fake, checkpoint="2024-05-31 00:00:00")

    await servicenow_connector._sync_articles()

    # page 1 and a0001's stored attachment are left as they are, not rewritten with "no attachments"
    assert sorted(written_ids(servicenow_connector)) == ["KB0000100", file_key(100, "KB0000100")]
    assert servicenow_connector.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_a_failed_full_attachment_listing_fails_every_article(servicenow_connector) -> None:
    fake = FakeServiceNow(
        {"kb_knowledge": [article_row(i, f"2024-06-01 00:{i // 100:02d}:00") for i in range(150)],
         "sys_attachment": [attachment_row(1, "a0001")]},
        fail=lambda table, query: status_error(503) if table == "sys_attachment" else None,
    )
    wire_listing(servicenow_connector, fake)

    await servicenow_connector._sync_articles()

    assert written_ids(servicenow_connector) == []
    assert len(fake.calls_to("sys_attachment")) == 1  # not tried again for every page
    assert servicenow_connector.full_sync_coverage().complete is False
    assert servicenow_connector.article_sync_point.data["articles"]["last_sync_time"] == "2024-05-31 23:59:59"


# --- attachment MIME types -------------------------------------------------------------------


def _attachment_meta(file_name: str, content_type: str) -> AttachmentMetadata:
    return AttachmentMetadata(
        sys_id="att1", file_name=file_name, content_type=content_type, size_bytes="10", table_sys_id="a1",
        sys_created_on="2024-01-01 00:00:00", sys_updated_on="2024-01-01 00:00:00",
    )


@pytest.mark.parametrize(("file_name", "content_type", "mime", "extension"), [
    ("Mail.EML", "application/octet-stream", "message/rfc822", "eml"),
    ("clip.mp4", "video/mp4", "video/mp4", "mp4"),
    ("data.xml", "text/xml", "text/xml", "xml"),
    ("blob", "", MimeTypes.UNKNOWN.value, None),
    ("thing.zzz-unknown", "application/unknown", MimeTypes.UNKNOWN.value, "zzz-unknown"),
])
def test_unknown_mime_kept_with_guessed_type(servicenow_connector, file_name, content_type, mime, extension) -> None:
    record = servicenow_connector._transform_to_attachment_file_record(_attachment_meta(file_name, content_type))

    assert record is not None
    assert (record.mime_type, record.extension) == (mime, extension)


def test_charset_parameter_stripped(servicenow_connector) -> None:
    record = servicenow_connector._transform_to_attachment_file_record(
        _attachment_meta("notes.txt", "Text/Plain;charset=UTF-8"))

    assert record.mime_type == "text/plain"


# --- a full sync reads everything, whatever the sync points say -----------------------------


@pytest.mark.asyncio
async def test_full_sync_ignores_stored_checkpoints(servicenow_connector) -> None:
    sc = servicenow_connector
    fake = FakeServiceNow()
    wire_listing(sc, fake, checkpoint=T0)
    stored = {"last_sync_time": T0}
    sc.user_sync_point = MemorySyncPoint({"users": stored})
    sc.category_sync_point = MemorySyncPoint({"categories": stored})
    company = ORGANIZATIONAL_ENTITIES["company"]
    sc.org_entity_sync_points = {"company": MemorySyncPoint({company["sync_point_key"]: stored})}
    sc.data_entities_processor.full_sync_running = MagicMock(return_value=True)

    await sc._sync_users()
    await sc._sync_categories()
    await sc._sync_single_organizational_entity("company", company)
    await sc._sync_articles()

    for table in ("sys_user", "kb_category", "core_company", "kb_knowledge"):
        assert all("sys_updated_on>" not in q for q in fake.calls_to(table))
    # no Cannot Read re-read pass either; the other listing is the full sync's reconcile (ids only)
    assert [limit for t, _, limit in fake.calls if t == "kb_knowledge"] == [100, 1000]
    sc.data_entities_processor.full_sync_running.assert_called_with(sc.connector_id)


# --- reindex ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reindex_records_requeues_all_including_unchanged(servicenow_connector) -> None:
    records = [MagicMock(external_revision_id="same"), MagicMock(external_revision_id="same")]

    await servicenow_connector.reindex_records(records)

    servicenow_connector.data_entities_processor.reindex_existing_records.assert_awaited_once_with(records)
    servicenow_connector.data_entities_processor.on_new_records.assert_not_called()


# --- an inheriting article with no grants of its own ----------------------------------------


@pytest.mark.asyncio
async def test_inherit_only_article_without_author_is_written(servicenow_connector) -> None:
    wire_listing(servicenow_connector, FakeServiceNow({"kb_knowledge": [article_row(1, author="")]}))

    await servicenow_connector._sync_articles()

    [(record, permissions)] = servicenow_connector.data_entities_processor.on_new_records.await_args.args[0]
    assert (record.external_record_id, record.inherit_permissions, permissions) == ("KB0000001", True, [])


# --- what a full sync did not see, it does not unlink --------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("count", "hidden"), [(500, set(range(1, 501))), (3, {2})])
async def test_articles_an_acl_hides_make_the_full_sync_incomplete(servicenow_connector, count, hidden) -> None:
    # ServiceNow counts every row but shows the account none of them, or not all.
    fake = FakeServiceNow({"kb_knowledge": [article_row(i, _acl_hidden=i in hidden) for i in range(1, count + 1)]})
    store = wire_store(servicenow_connector, fake, stored_record("KB0000002"))

    await servicenow_connector._sync_articles()

    assert servicenow_connector.full_sync_coverage().complete is False
    assert "KB0000002" in store.records


@pytest.mark.asyncio
async def test_hidden_kb_that_is_kept_makes_the_full_sync_incomplete(servicenow_connector) -> None:
    sc = servicenow_connector
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb1", "kb2"]}})
    store = wire_store(sc, FakeServiceNow({"kb_knowledge_base": [kb_row("kb1"), kb_row("kb2", _acl_hidden=True)]}))

    await sc._sync_knowledge_bases([])

    assert store.deleted_groups == []
    assert sc.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_attachment_an_acl_hides_is_not_pruned(servicenow_connector) -> None:
    fake = FakeServiceNow({"kb_knowledge": [article_row(1)], "sys_attachment": [
        attachment_row(1, "a0001"), {**attachment_row(2, "a0001"), "_acl_hidden": True}]})
    store = wire_store(servicenow_connector, fake, stored_record("f0002", "FILE", parent="KB0000001"))

    await servicenow_connector._sync_articles()

    assert store.keys() == ["KB0000001", file_key(1), "f0002"]
    assert servicenow_connector.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_article_whose_transform_raises_is_failed_not_dropped(servicenow_connector) -> None:
    sc = servicenow_connector
    wire_listing(sc, _three_articles_second_fails(fail=False))
    body = sc._article_body

    def broken_second(row: TableAPIRecord) -> str:
        if row.number == "KB0000002":
            raise ValueError("bad row")
        return body(row)

    with patch.object(sc, "_article_body", side_effect=broken_second):
        await sc._sync_articles()

    assert sorted(written_ids(sc)) == ["KB0000001", "KB0000003"]
    assert sc.article_sync_point.data["articles"]["last_sync_time"] == "2024-06-01 01:59:59"
    assert sc.full_sync_coverage().complete is False


# --- a full read that stops continues where it stopped ----------------------------------------------


def _timed_articles(count: int) -> list[dict]:
    return [article_row(i, f"2024-06-01 00:{i // 60:02d}:{i % 60:02d}") for i in range(1, count + 1)]


def _stop_on_listing_call(n: int) -> tuple[dict, object]:
    """A fail hook that spends the rate limit on the n-th call listing current articles, while ``stop["on"]``."""
    stop = {"on": True, "calls": 0}

    def fail(table: str, query: str | None) -> Exception | None:
        if table == "kb_knowledge" and (query or "").startswith("workflow_state=published") and stop["on"]:
            stop["calls"] += 1
            if stop["calls"] == n:
                return ServiceNowRateLimited(time.time() + 600)
        return None

    return stop, fail


@pytest.mark.asyncio
async def test_a_stopped_full_read_resumes_where_it_stopped_and_unlinks_only_after_the_whole_pass(servicenow_connector) -> None:
    sc = servicenow_connector
    rows = _timed_articles(250)  # three pages of 100
    stop, fail = _stop_on_listing_call(2)
    fake = FakeServiceNow({"kb_knowledge": rows}, fail=fail)
    point = migrated_sync_point(**{"articles_reconcile/last": {"last_sync_time": "2000-01-01 00:00:00"}})
    store = wire_store(sc, fake, stored_record("KB9999999"), sync_point=point)

    with pytest.raises(ServiceNowRateLimited):
        await sc._sync_articles()

    # The first page is written; nothing is unlinked; the next run starts after its last row.
    assert store.keys("WEBPAGE") == sorted([f"KB{i:07d}" for i in range(1, 101)] + ["KB9999999"])
    assert store.deleted == []
    saved = point.data["articles"]
    assert saved["full_resume_after"] == [rows[99]["sys_updated_on"], rows[99]["sys_id"]]
    assert "last_sync_time" not in saved

    stop["on"] = False
    fake.calls.clear()
    sc._sync_read_incomplete = False

    await sc._sync_articles()

    listed = [q for q in fake.calls_to("kb_knowledge") if q.startswith("workflow_state=published")]
    assert f"sys_updated_on>{rows[99]['sys_updated_on']}^NQ" in listed[0]  # not from the start
    assert len(listed) == 2 + 1  # pages 101-200 and 201-250, then the reconcile's id listing
    assert store.keys("WEBPAGE") == [f"KB{i:07d}" for i in range(1, 251)]
    # The reconcile, after the whole pass, removes the article gone at the source; a full sync of
    # the platform unlinks nothing after a resumed run, which did not see the first part.
    assert store.deleted == ["KB9999999"]
    assert sc.full_sync_coverage().complete is False
    saved = point.data["articles"]
    assert (saved["last_sync_time"], saved["full_resume_after"]) == (rows[-1]["sys_updated_on"], [])
    # the rows the next delta's overlap reads again (the last 120 s), synced already
    assert saved["synced_in_overlap"] == sorted(f"{r['sys_updated_on']}|{r['sys_id']}" for r in rows[130:])


@pytest.mark.asyncio
async def test_a_resume_point_is_never_past_a_failed_article(servicenow_connector) -> None:
    sc = servicenow_connector
    rows = _timed_articles(250)
    _, fail = _stop_on_listing_call(3)
    fake = FakeServiceNow({"kb_knowledge": rows}, fail=fail)
    wire_store(sc, fake)
    real = sc._process_single_article

    async def first_page_fails_once(article: TableAPIRecord, *args: object) -> list:
        if article.number == "KB0000050":
            raise RuntimeError("write failed")
        return await real(article, *args)

    sc._process_single_article = first_page_fails_once

    with pytest.raises(ServiceNowRateLimited):
        await sc._sync_articles()

    assert "articles" not in sc.article_sync_point.data  # the next run reads from the start again


@pytest.mark.asyncio
async def test_a_platform_full_sync_continues_a_stopped_one_and_unlinks_only_after_a_whole_pass(servicenow_connector) -> None:
    sc = servicenow_connector
    rows = _timed_articles(250)
    stop, fail = _stop_on_listing_call(2)
    fake = FakeServiceNow({"kb_knowledge": rows}, fail=fail)
    point = migrated_sync_point(**{"articles_reconcile/last": {"last_sync_time": "2000-01-01 00:00:00"}})
    store = wire_store(sc, fake, sync_point=point)
    sc.data_entities_processor.full_sync_running = MagicMock(return_value=True)  # every run here

    with pytest.raises(ServiceNowRateLimited):  # over the cap at the second page
        await sc._sync_articles()
    assert len(store.keys("WEBPAGE")) == 100
    stop["on"] = False
    fake.calls.clear()
    sc._sync_read_incomplete = False

    await sc._sync_articles()  # the rerun the platform owes, as a full sync

    listed = [q for q in fake.calls_to("kb_knowledge") if q.startswith("workflow_state=published")]
    assert f"sys_updated_on>{rows[99]['sys_updated_on']}^NQ" in listed[0]  # continues where it stopped
    assert store.keys("WEBPAGE") == [f"KB{i:07d}" for i in range(1, 251)]
    assert sc.full_sync_coverage().complete is False  # did not see the first part: unlinks nothing
    assert point.data["articles"]["full_resume_after"] == []

    fake.calls.clear()
    sc._sync_read_incomplete = False

    await sc._sync_articles()  # the full sync the platform owes after an incomplete one

    assert "^NQ" not in fake.calls_to("kb_knowledge")[0]  # the incremental checkpoint is not used
    assert sc.full_sync_coverage().complete is True  # a whole pass: now it unlinks


@pytest.mark.asyncio
async def test_the_resumed_run_keeps_the_first_parts_change_checkpoints(servicenow_connector) -> None:
    sc = servicenow_connector
    rows = _timed_articles(150)
    _, fail = _stop_on_listing_call(2)
    fake = FakeServiceNow({"kb_knowledge": rows, "kb_feedback": [{"sys_id": "fb1", "article": "a0001", "sys_updated_on": T0}]},
                          fail=fail)
    wire_store(sc, fake)
    with pytest.raises(ServiceNowRateLimited):
        await sc._sync_articles()
    first = dict(sc.article_sync_point.data["articles"])
    fake.fail = None

    await sc._sync_articles()

    # Feedback and inline images that changed while the first part's pages were read are looked for next run.
    assert sc.article_sync_point.data["feedback"] == {"last_sync_time": first["feedback"], "count": first["count"]}
    assert sc.article_sync_point.data["attachments"] == {"last_sync_time": first["attachments"]}
    assert "access_fingerprint" not in sc.article_sync_point.data  # so the next run re-reads the articles with criteria


@pytest.mark.asyncio
async def test_a_keyset_query_may_not_hold_nq_or_orderby(servicenow_connector) -> None:
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=FakeServiceNow({}))
    for query in ("active=true^NQactive=false", "active=true^ORDERBYname"):
        with pytest.raises(AssertionError):
            [p async for p in servicenow_connector._iter_table("kb_knowledge", query, ["sys_id"])]
