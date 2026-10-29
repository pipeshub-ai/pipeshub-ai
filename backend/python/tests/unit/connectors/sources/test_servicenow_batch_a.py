"""ServiceNow sync reads every row, keeps checkpoints behind failures and reports partial runs.

The connector's listings run against ``FakeServiceNow``, which answers encoded queries the way the
Table API does, including pages that come back short because an ACL hid rows after the page was cut.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest
from servicenow_fakes import (
    FakeServiceNow,
    MemorySyncPoint,
    migrated_sync_point,
    status_error,
)
from test_servicenow_connector import (  # noqa: F401
    mock_config_service,
    mock_data_entities_processor,
    mock_data_store_provider,
    mock_logger,
    servicenow_connector,
)

from app.config.constants.arangodb import Connectors, MimeTypes
from app.connectors.sources.servicenow.servicenow.constants import (
    ORGANIZATIONAL_ENTITIES,
    ServiceNowDefaults,
)
from app.models.entities import AppUser
from app.sources.external.servicenow.models import (
    AttachmentMetadata,
    ServiceNowAPIError,
    TableAPIResponse,
)

T0 = "2024-06-01 00:00:00"
INSTANCE = "https://dev00000.service-now.com"


def _article(n: int, updated: str = T0, **fields: str) -> dict:
    return {
        "sys_id": f"a{n:04d}", "number": f"KB{n:07d}", "short_description": f"Article {n}", "text": "<p>body</p>",
        "kb_knowledge_base": "kb1", "kb_knowledge_base.active": "true", "kb_category": "cat1",
        "workflow_state": "published", "active": "true",
        "sys_created_on": "2024-01-01 00:00:00", "sys_updated_on": updated, **fields,
    }


def _attachment(n: int, article: str) -> dict:
    return {
        "sys_id": f"f{n:04d}", "file_name": f"file{n}.pdf", "content_type": "application/pdf", "size_bytes": "10",
        "table_name": "kb_knowledge", "table_sys_id": article,
        "sys_created_on": "2024-01-01 00:00:00", "sys_updated_on": "2024-01-01 00:00:00",
    }


def _wire(sc, fake: FakeServiceNow, checkpoint: str | None = None) -> None:
    sc._get_fresh_datasource = AsyncMock(return_value=fake)
    sc.instance_url = INSTANCE
    sc.article_sync_point = migrated_sync_point(checkpoint)


def _written(sc) -> list[str]:
    return [r.external_record_id for c in sc.data_entities_processor.on_new_records.await_args_list for r, _ in c.args[0]]


# --- SN-01: a short page does not end a listing (X-Total-Count does: test_servicenow_batch_f) ---


@pytest.mark.asyncio
async def test_acl_short_page_does_not_end_listing(servicenow_connector) -> None:  # noqa: F811
    groups = [{"sys_id": f"g{i:03d}", "name": f"G{i}", "sys_updated_on": T0} for i in range(103)]
    for hidden in (10, 50, 90):  # page 1 comes back with 97 of its 100 rows
        groups[hidden]["_acl_hidden"] = True
    fake = FakeServiceNow({"sys_user_group": groups})
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)

    result = await servicenow_connector._fetch_all_groups()

    assert len(result) == 100  # every visible row, page 2 included
    assert {g.sys_id for g in result} == {g["sys_id"] for g in groups if not g.get("_acl_hidden")}


@pytest.mark.asyncio
async def test_listing_that_does_not_advance_raises(servicenow_connector) -> None:  # noqa: F811
    # An instance that ignores the keyset condition answers every page with the first one.
    same = TableAPIResponse(result=[{"sys_id": "g1", "sys_updated_on": T0}], total_count=1000)
    servicenow_connector._get_fresh_datasource = AsyncMock(
        return_value=MagicMock(get_now_table_tableName=AsyncMock(return_value=same)))

    with pytest.raises(ServiceNowAPIError, match="did not advance"):
        await servicenow_connector._fetch_all_groups()


# --- SN-02: keyset paging and the delta overlap ---------------------------------------------


@pytest.mark.asyncio
async def test_keyset_paging_reads_every_tied_row(servicenow_connector) -> None:  # noqa: F811
    # 250 rows share one timestamp, and the instance returns ties in any order.
    groups = [{"sys_id": f"g{i:03d}", "name": f"G{i}", "sys_updated_on": T0} for i in range(250)]
    fake = FakeServiceNow({"sys_user_group": groups}, shuffle_ties=True)
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)

    result = await servicenow_connector._fetch_all_groups()

    assert sorted(g.sys_id for g in result) == [g["sys_id"] for g in groups]


@pytest.mark.asyncio
async def test_row_updated_during_walk_is_not_skipped(servicenow_connector) -> None:  # noqa: F811
    groups = [{"sys_id": f"g{i:03d}", "name": f"G{i}", "sys_updated_on": T0} for i in range(150)]

    def edit_a_read_row(fake: FakeServiceNow) -> None:
        # after page 1, a row it returned is updated, which moves it to the end of the order
        groups[0]["sys_updated_on"] = "2024-06-02 00:00:00"

    fake = FakeServiceNow({"sys_user_group": groups}, after_call=edit_a_read_row)
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)

    result = await servicenow_connector._fetch_all_groups()

    assert {g.sys_id for g in result} == {g["sys_id"] for g in groups}  # offsets would skip g100


@pytest.mark.asyncio
async def test_delta_reads_with_overlap_window(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"kb_knowledge": [
        _article(1, "2024-05-31 23:57:00"),  # before the overlap window: already synced
        _article(2, "2024-05-31 23:59:30"),  # committed late, dated just before the checkpoint
        _article(3, T0),  # the checkpoint's own second
        _article(4, "2024-06-01 10:00:00"),
    ]})
    _wire(servicenow_connector, fake, checkpoint=T0)

    await servicenow_connector._sync_articles()

    assert "sys_updated_on>2024-05-31 23:58:00" in fake.calls_to("kb_knowledge")[0]
    assert sorted(_written(servicenow_connector)) == ["KB0000002", "KB0000003", "KB0000004"]
    saved = servicenow_connector.article_sync_point.data["articles"]["last_sync_time"]
    assert saved == "2024-06-01 10:00:00"


# --- SN-03: paged single reads and chunked IN lists -----------------------------------------


@pytest.mark.asyncio
async def test_in_list_is_chunked_under_max_url_length(servicenow_connector) -> None:  # noqa: F811
    ids = [f"{i:032x}" for i in range(100)]
    fake = FakeServiceNow({"user_criteria": [{"sys_id": i, "sys_updated_on": T0, "user": "u1"} for i in ids]})
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)

    rows = await servicenow_connector._fetch_user_criteria(ids)

    assert sorted(r.sys_id for r in rows) == ids
    assert len(fake.calls) > 2
    fields = "sys_id,active,match_all,advanced,user,group,role,department,location,company,cost_center,sys_updated_on"
    for _, query, limit in fake.calls:
        url = (f"{INSTANCE}/api/now/table/user_criteria?sysparm_query={quote(query, safe='')}"
               f"&sysparm_fields={quote(fields, safe='')}&sysparm_limit={limit}&sysparm_offset=0"
               "&sysparm_display_value=false&sysparm_exclude_reference_link=true")
        assert len(url) < ServiceNowDefaults.MAX_URL_LENGTH


@pytest.mark.asyncio
async def test_admin_read_is_paged(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow({"sys_user_has_role": [
        {"sys_id": f"r{i:03d}", "user": f"u{i:03d}", "role.name": "admin", "user.active": "true", "sys_updated_on": T0}
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


# --- SN-04 / SN-05 / SN-06: failures hold the checkpoint and the coverage -------------------


@pytest.mark.asyncio
async def test_failed_article_makes_full_sync_incomplete(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow(
        {"kb_knowledge": [_article(1)]},
        fail=lambda table, query: status_error(500) if table == "sys_attachment" else None,
    )
    _wire(servicenow_connector, fake)

    await servicenow_connector._sync_articles()

    assert servicenow_connector.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_unreadable_kb_criteria_makes_full_sync_incomplete(servicenow_connector) -> None:  # noqa: F811
    fake = FakeServiceNow(
        {"kb_knowledge_base": [{"sys_id": "kb1", "title": "KB", "sys_updated_on": T0}]},
        fail=lambda table, query: status_error(503) if table == "kb_uc_can_read_mtom" else None,
    )
    servicenow_connector._get_fresh_datasource = AsyncMock(return_value=fake)
    servicenow_connector.data_entities_processor.note_kept_record_group = AsyncMock()

    await servicenow_connector._sync_knowledge_bases([])

    assert servicenow_connector.full_sync_coverage().complete is False


@pytest.mark.asyncio
async def test_clean_run_reports_complete(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    _wire(sc, FakeServiceNow({"kb_knowledge": [_article(1)], "sys_attachment": [_attachment(1, "a0001")]}))
    sc.servicenow_client = MagicMock()
    sc._sync_read_incomplete = True  # left over from the previous run
    with patch.object(sc, "_register_authenticated_identity", new_callable=AsyncMock), \
         patch.object(sc, "_sync_users_and_groups", new_callable=AsyncMock), \
         patch.object(sc, "_get_admin_users", new_callable=AsyncMock, return_value=[]), \
         patch.object(sc, "_sync_knowledge_bases", new_callable=AsyncMock), \
         patch.object(sc, "_sync_categories", new_callable=AsyncMock):
        await sc.run_sync()

    assert sorted(_written(sc)) == ["KB0000001", "f0001"]
    assert sc.full_sync_coverage().complete is True


def _three_articles_second_fails(fail: bool) -> FakeServiceNow:
    # Attachments are listed per page, so the second article fails on its own Can Read criteria.
    return FakeServiceNow(
        {
            "kb_knowledge": [
                _article(1, "2024-06-01 01:00:00"),
                _article(2, "2024-06-01 02:00:00", can_read_user_criteria="uc2"),
                _article(3, "2024-06-01 03:00:00"),
            ],
            "user_criteria": [{"sys_id": "uc2", "user": "u1", "sys_updated_on": T0}],
        },
        fail=lambda table, query: status_error(503) if fail and table == "user_criteria" else None,
    )


@pytest.mark.asyncio
async def test_checkpoint_held_before_oldest_failed_article(servicenow_connector) -> None:  # noqa: F811
    _wire(servicenow_connector, _three_articles_second_fails(fail=True))

    await servicenow_connector._sync_articles()

    assert sorted(_written(servicenow_connector)) == ["KB0000001", "KB0000003"]
    assert servicenow_connector.article_sync_point.data["articles"]["last_sync_time"] == "2024-06-01 01:59:59"


@pytest.mark.asyncio
async def test_failed_article_retried_next_run(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    _wire(sc, _three_articles_second_fails(fail=True))
    await sc._sync_articles()
    sync_point = sc.article_sync_point

    sc.data_entities_processor.on_new_records.reset_mock()
    _wire(sc, _three_articles_second_fails(fail=False))
    sc.article_sync_point = sync_point
    await sc._sync_articles()

    assert "KB0000002" in _written(sc)
    assert sync_point.data["articles"]["last_sync_time"] == "2024-06-01 03:00:00"


@pytest.mark.asyncio
async def test_attachment_listing_503_fails_the_article_not_the_attachments(servicenow_connector) -> None:  # noqa: F811
    # Attachments are listed per page of articles, so a failed listing fails that page.
    fake = FakeServiceNow(
        {
            "kb_knowledge": [_article(i, f"2024-06-01 00:{i // 100:02d}:00") for i in range(101)],
            "sys_attachment": [_attachment(1, "a0001"), _attachment(100, "a0100")],
        },
        fail=lambda table, query: status_error(503) if table == "sys_attachment" and "a0001" in query else None,
    )
    _wire(servicenow_connector, fake)

    await servicenow_connector._sync_articles()

    # page 1 and a0001's stored attachment are left as they are, not rewritten with "no attachments"
    assert sorted(_written(servicenow_connector)) == ["KB0000100", "f0100"]
    assert servicenow_connector.full_sync_coverage().complete is False


# --- SN-07: attachment MIME types ------------------------------------------------------------


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
def test_unknown_mime_kept_with_guessed_type(servicenow_connector, file_name, content_type, mime, extension) -> None:  # noqa: F811
    record = servicenow_connector._transform_to_attachment_file_record(_attachment_meta(file_name, content_type))

    assert record is not None
    assert (record.mime_type, record.extension) == (mime, extension)


def test_charset_parameter_stripped(servicenow_connector) -> None:  # noqa: F811
    record = servicenow_connector._transform_to_attachment_file_record(
        _attachment_meta("notes.txt", "Text/Plain;charset=UTF-8"))

    assert record.mime_type == "text/plain"


# --- SN-08: a full sync reads everything, whatever the sync points say ----------------------


@pytest.mark.asyncio
async def test_full_sync_ignores_stored_checkpoints(servicenow_connector) -> None:  # noqa: F811
    sc = servicenow_connector
    fake = FakeServiceNow()
    _wire(sc, fake, checkpoint=T0)
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


# --- SN-09: reindex --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reindex_records_requeues_all_including_unchanged(servicenow_connector) -> None:  # noqa: F811
    records = [MagicMock(external_revision_id="same"), MagicMock(external_revision_id="same")]

    await servicenow_connector.reindex_records(records)

    servicenow_connector.data_entities_processor.reindex_existing_records.assert_awaited_once_with(records)
    servicenow_connector.data_entities_processor.on_new_records.assert_not_called()


# --- SN-10: an inheriting article with no grants of its own ---------------------------------


@pytest.mark.asyncio
async def test_inherit_only_article_without_author_is_written(servicenow_connector) -> None:  # noqa: F811
    _wire(servicenow_connector, FakeServiceNow({"kb_knowledge": [_article(1, author="")]}))

    await servicenow_connector._sync_articles()

    [(record, permissions)] = servicenow_connector.data_entities_processor.on_new_records.await_args.args[0]
    assert (record.external_record_id, record.inherit_permissions, permissions) == ("KB0000001", True, [])
