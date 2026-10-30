"""ServiceNow sync and indexing filters: knowledge bases and categories (In / Not in), modified and
created windows (UTC), what narrowing removes and widening adds, the indexing toggles and manual
indexing, the filter options (paging, search, a readable error), and a run that cannot read its filters.

The Table API is ``FakeServiceNow``; the stored records are ``FakeRecordStore``.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from servicenow_fakes import (
    T0,
    FakeServiceNow,
    MemorySyncPoint,
    article_row,
    attachment_row,
    category_row,
    file_key,
    groups_written,
    kb_row,
    migrated_sync_point,
    status_error,
    stored_record,
    wire_store,
)

from app.config.constants.arangodb import ProgressStatus
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector
from app.sources.client.servicenow.servicenow import ServiceNowRateLimited
from app.sources.external.servicenow.models import AttachmentMetadata, TableAPIRecord


def _ms(stamp: str) -> int:
    return int(datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)


def _sync_values(kbs=None, categories=None, modified=None, created=None) -> dict:
    """Sync filter values as the config stores them: (operator, ids) or (start, end) stamps."""
    values = {}
    for key, chosen in (("knowledge_bases", kbs), ("folder_ids", categories)):
        if chosen:
            values[key] = {"operator": chosen[0], "value": [{"id": i, "label": i} for i in chosen[1]],
                           "type": "multiselect"}
    for key, window in (("modified", modified), ("created", created)):
        if window:
            values[key] = {"operator": "is_between", "type": "datetime",
                           "value": {"start": window[0] and _ms(window[0]), "end": window[1] and _ms(window[1])}}
    return values


def _filters(sc, *, indexing: dict | None = None, **sync) -> None:
    sc.sync_filters = FilterCollection.from_dict(_sync_values(**sync))
    sc.indexing_filters = FilterCollection.from_dict(
        {k: {"operator": "is", "value": v, "type": "boolean"} for k, v in (indexing or {}).items()})


def _full_sync(sc) -> None:
    """The run the platform queues after a sync filter is saved."""
    sc.data_entities_processor.full_sync_running = MagicMock(return_value=True)


def _number(n: int) -> str:
    return f"KB{n:07d}"


# --- the declared filters ----------------------------------------------------------------------


def test_builder_declares_the_filters_once() -> None:
    filters = ServiceNowConnector._connector_metadata["config"]["filters"]
    sync = {f["name"]: f for f in filters["sync"]["schema"]["fields"]}
    indexing = [f["name"] for f in filters["indexing"]["schema"]["fields"]]

    assert sorted(sync) == ["created", "folder_ids", "knowledge_bases", "modified"]
    assert sync["knowledge_bases"]["optionSourceType"] == sync["folder_ids"]["optionSourceType"] == "dynamic"
    assert sorted(indexing) == ["attachments", "enable_manual_sync", "knowledge_base"]


# --- knowledge bases ---------------------------------------------------------------------------


_TWO_KBS = {"kb_knowledge": [article_row(1), article_row(2, kb_knowledge_base="kb2")]}
# Knowledge base sys_ids as an instance makes them: only such values reach a query.
K1, K2 = f"{0xB1:032x}", f"{0xB2:032x}"
_TWO_HEX_KBS = {"kb_knowledge": [article_row(1, kb_knowledge_base=K1), article_row(2, kb_knowledge_base=K2)]}


@pytest.mark.asyncio
async def test_kb_in_filter_query(servicenow_connector) -> None:
    sc = servicenow_connector
    _filters(sc, kbs=("in", [K1]))
    fake = FakeServiceNow(_TWO_HEX_KBS)
    store = wire_store(sc, fake)

    await sc._sync_articles()

    assert store.keys() == [_number(1)]
    assert all(f"^kb_knowledge_baseIN{K1}" in q for q in fake.calls_to("kb_knowledge"))


@pytest.mark.asyncio
async def test_kb_not_in_filter_query(servicenow_connector) -> None:
    sc = servicenow_connector
    _filters(sc, kbs=("not_in", [K1]))
    fake = FakeServiceNow(_TWO_HEX_KBS)
    store = wire_store(sc, fake)

    await sc._sync_articles()

    assert store.keys() == [_number(2)]
    assert all(f"^kb_knowledge_baseNOT IN{K1}" in q for q in fake.calls_to("kb_knowledge"))


@pytest.mark.asyncio
async def test_kb_filter_value_that_is_no_sys_id_is_dropped(servicenow_connector) -> None:
    sc = servicenow_connector
    _filters(sc, kbs=("in", [K1, "x^NQworkflow_state=draft"]))
    fake = FakeServiceNow(_TWO_HEX_KBS)
    store = wire_store(sc, fake)

    await sc._sync_articles()

    assert store.keys() == [_number(1)]
    queries = fake.calls_to("kb_knowledge")
    assert all(f"^kb_knowledge_baseIN{K1}^" in q for q in queries)
    assert not any("workflow_state=draft" in q for q in queries)


@pytest.mark.asyncio
async def test_kb_list_too_long_for_the_url_is_checked_per_row(servicenow_connector) -> None:
    sc = servicenow_connector
    _filters(sc, kbs=("in", ["kb1", *(f"{i:032x}" for i in range(20))]))
    fake = FakeServiceNow(_TWO_KBS)
    store = wire_store(sc, fake)

    await sc._sync_articles()

    assert store.keys() == [_number(1)]
    assert not any("kb_knowledge_baseIN" in q for q in fake.calls_to("kb_knowledge"))


@pytest.mark.asyncio
async def test_kb_filter_scopes_the_kb_listing(servicenow_connector) -> None:
    sc = servicenow_connector
    _filters(sc, kbs=("not_in", ["kb2"]))
    wire_store(sc, FakeServiceNow({"kb_knowledge_base": [kb_row("kb1"), kb_row("kb2")],
                              "kb_category": [category_row("c1", "kb1"), category_row("c2", "kb2")]}))

    await sc._sync_knowledge_bases([])
    await sc._sync_categories()

    assert groups_written(sc) == ["c1", "kb1"]
    assert sc._live_kb_ids == {"kb1"}


# --- categories --------------------------------------------------------------------------------

# kb1 > c1 > c1a > c1a1, kb1 > c2, and articles in each of them and in none
_TREE = {
    "kb_knowledge_base": [kb_row("kb1")],
    "kb_category": [category_row("c1", "kb1"), category_row("c1a", "c1"), category_row("c1a1", "c1a"), category_row("c2", "kb1")],
    "kb_knowledge": [
        article_row(1, kb_category="c1"), article_row(2, kb_category="c1a"), article_row(3, kb_category="c1a1"),
        article_row(4, kb_category="c2"), article_row(5, kb_category=""),
    ],
}


async def _sync_tree(sc) -> None:
    await sc._sync_knowledge_bases([])
    await sc._sync_categories()
    await sc._sync_articles()


@pytest.mark.asyncio
async def test_category_in_includes_descendants_and_ancestors(servicenow_connector) -> None:
    sc = servicenow_connector
    _filters(sc, categories=("in", ["c1a"]))
    fake = FakeServiceNow(_TREE)
    store = wire_store(sc, fake)

    await _sync_tree(sc)

    # c1 is written so the tree under kb1 is whole; its own article is not in scope
    assert groups_written(sc) == ["c1", "c1a", "c1a1", "kb1"]
    assert store.keys() == [_number(2), _number(3)]


@pytest.mark.asyncio
async def test_category_not_in_keeps_uncategorised(servicenow_connector) -> None:
    sc = servicenow_connector
    _filters(sc, categories=("not_in", ["c1a"]))
    fake = FakeServiceNow(_TREE)
    store = wire_store(sc, fake)

    await _sync_tree(sc)

    assert groups_written(sc) == ["c1", "c2", "kb1"]
    assert store.keys() == [_number(1), _number(4), _number(5)]


# --- date windows ------------------------------------------------------------------------------


_DATED = {"kb_knowledge": [
    article_row(1, "2024-02-01 00:00:00", sys_created_on="2023-06-01 00:00:00"),
    article_row(2, "2024-04-01 00:00:00", sys_created_on="2024-03-15 00:00:00"),
    article_row(3, "2024-06-01 00:00:00", sys_created_on="2024-05-01 00:00:00"),
]}


@pytest.mark.asyncio
async def test_modified_window_utc_literal(servicenow_connector) -> None:
    sc = servicenow_connector
    _filters(sc, modified=("2024-03-01 00:00:00", "2024-05-01 00:00:00"))
    fake = FakeServiceNow(_DATED)
    store = wire_store(sc, fake)

    await sc._sync_articles()

    assert store.keys() == [_number(2)]
    assert "^sys_updated_on>=2024-03-01 00:00:00^sys_updated_on<=2024-05-01 00:00:00" in fake.calls_to("kb_knowledge")[0]


@pytest.mark.asyncio
async def test_created_window_utc_literal(servicenow_connector) -> None:
    sc = servicenow_connector
    _filters(sc, created=("2024-01-01 00:00:00", None))
    fake = FakeServiceNow(_DATED)
    store = wire_store(sc, fake)

    await sc._sync_articles()

    assert store.keys() == [_number(2), _number(3)]
    query = fake.calls_to("kb_knowledge")[0]
    assert "^sys_created_on>=2024-01-01 00:00:00" in query and "sys_created_on<=" not in query


@pytest.mark.asyncio
async def test_edit_that_leaves_the_window_deletes_on_incremental(servicenow_connector) -> None:
    # The delta lists the changed row in any state; its resolution is scoped like the full read.
    sc = servicenow_connector
    _filters(sc, modified=(None, "2024-05-01 00:00:00"))
    fake = FakeServiceNow({"kb_knowledge": [article_row(1, "2024-06-02 00:00:00")]})
    store = wire_store(sc, fake, stored_record(_number(1)), stored_record("f1", "FILE", parent=_number(1)),
                  sync_point=migrated_sync_point(T0))

    await sc._sync_articles()

    assert store.keys() == []


@pytest.mark.asyncio
async def test_filters_apply_to_the_reread_pass(servicenow_connector) -> None:
    # The articles with their own criteria are re-read every run: only those in scope.
    sc = servicenow_connector
    _filters(sc, kbs=("in", [K1]))
    fake = FakeServiceNow({"kb_knowledge": [
        article_row(1, "2024-05-01 00:00:00", kb_knowledge_base=K1, can_read_user_criteria="uc1"),
        article_row(2, "2024-05-01 00:00:00", kb_knowledge_base=K2, can_read_user_criteria="uc1"),
    ], "user_criteria": [{"sys_id": "uc1", "user": "u1", "active": "true", "sys_updated_on": T0}]})
    store = wire_store(sc, fake, sync_point=migrated_sync_point(T0))

    await sc._sync_articles()

    reread = [q for q in fake.calls_to("kb_knowledge") if "can_read_user_criteriaISNOTEMPTY" in q]
    assert reread and all(f"^kb_knowledge_baseIN{K1}" in q for q in reread)
    assert store.keys() == [_number(1)]


@pytest.mark.asyncio
async def test_changed_inline_image_resolves_only_in_scope_articles(servicenow_connector) -> None:
    sc = servicenow_connector
    sc._category_filter = (False, {"c9"})  # set by _sync_categories from a Not in [c9] filter
    fake = FakeServiceNow({
        "kb_knowledge": [article_row(1, "2024-05-01 00:00:00"), article_row(2, "2024-05-01 00:00:00", kb_category="c9")],
        "sys_attachment": [{**attachment_row(9, "a0001"), "table_name": "ZZ_YYkb_knowledge", "sys_updated_on": "2024-06-02 00:00:00"},
                           {**attachment_row(8, "a0002"), "table_name": "ZZ_YYkb_knowledge", "sys_updated_on": "2024-06-02 00:00:00"}],
    })
    store = wire_store(sc, fake, sync_point=migrated_sync_point(T0, attachments={"last_sync_time": T0}))

    await sc._sync_articles()

    assert store.keys() == [_number(1)]


# --- narrowing removes, widening adds ----------------------------------------------------------


@pytest.mark.asyncio
async def test_narrowed_kb_filter_deletes_kb_records(servicenow_connector) -> None:
    sc = servicenow_connector
    _filters(sc, kbs=("in", ["kb1"]))
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb1", "kb2"]}})
    store = wire_store(sc, FakeServiceNow({"kb_knowledge_base": [kb_row("kb1"), kb_row("kb2")]}),
                  stored_record(_number(1), group="kb1"), stored_record(_number(2), group="kb2"))

    await sc._sync_knowledge_bases([])

    assert store.deleted_groups == ["kb2"]
    assert store.keys() == [_number(1)]
    assert sc._scopes_to_store == {"kb_scope": ["kb1"]}  # stored once the articles are synced


@pytest.mark.asyncio
async def test_hidden_kb_the_filter_leaves_out_is_removed(servicenow_connector) -> None:
    # An ACL hides kb2 from the listing; it still exists, but the filter leaves it out.
    sc = servicenow_connector
    _filters(sc, kbs=("not_in", [K2]))
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": [K1, K2]}})
    store = wire_store(sc, FakeServiceNow({"kb_knowledge_base": [kb_row(K1), kb_row(K2, _acl_hidden=True)]}))

    await sc._sync_knowledge_bases([])

    assert store.deleted_groups == [K2]


@pytest.mark.asyncio
async def test_narrowed_category_filter_deletes_out_of_scope(servicenow_connector) -> None:
    sc = servicenow_connector
    _full_sync(sc)
    _filters(sc, categories=("in", ["c1a"]))
    sc.category_sync_point = MemorySyncPoint({"kb_scope": {"ids": ["kb1"]},
                                              "category_scope": {"ids": ["c1", "c1a", "c1a1", "c2"]}})
    store = wire_store(sc, FakeServiceNow(_TREE), *(stored_record(_number(n), group=g) for n, g in
                                               ((1, "c1"), (2, "c1a"), (3, "c1a1"), (4, "c2"), (5, "kb1"))))

    await _sync_tree(sc)

    assert store.deleted_groups == ["c2"]
    # the article in c1 (kept for the tree) and the uncategorised one go with the reconcile
    assert store.keys() == [_number(2), _number(3)]


@pytest.mark.asyncio
async def test_narrowed_date_window_deletes_old_articles(servicenow_connector) -> None:
    sc = servicenow_connector
    _full_sync(sc)
    _filters(sc, modified=("2024-03-01 00:00:00", None))
    store = wire_store(sc, FakeServiceNow(_DATED), stored_record(_number(1)), stored_record("f1", "FILE", parent=_number(1)),
                  stored_record(_number(2)))

    await sc._sync_articles()

    assert store.keys() == [_number(2), _number(3)]
    assert "f1" in store.deleted  # with its attachment


@pytest.mark.asyncio
async def test_filter_that_matches_nothing_removes_every_article(servicenow_connector) -> None:
    # An empty listing is trusted when ServiceNow counts no row for the filtered query.
    sc = servicenow_connector
    _full_sync(sc)
    _filters(sc, modified=("2030-01-01 00:00:00", None))
    store = wire_store(sc, FakeServiceNow(_DATED), stored_record(_number(1)))

    await sc._sync_articles()

    assert store.keys() == []


@pytest.mark.asyncio
async def test_empty_listing_with_hidden_rows_removes_nothing(servicenow_connector) -> None:
    # Counted but not shown is the account, not the filter: nothing goes.
    sc = servicenow_connector
    _full_sync(sc)
    _filters(sc, kbs=("in", ["kb1"]))
    store = wire_store(sc, FakeServiceNow({"kb_knowledge": [article_row(1, _acl_hidden=True)]}), stored_record(_number(1)))

    await sc._sync_articles()

    assert store.keys() == [_number(1)]


@pytest.mark.asyncio
async def test_no_scope_removal_after_failed_listing(servicenow_connector) -> None:
    sc = servicenow_connector
    _full_sync(sc)
    _filters(sc, kbs=("in", ["kb1"]))
    fake = FakeServiceNow(_TWO_KBS)
    # the reconcile's listing (1,000 ids a page) fails
    fake.fail = lambda table, q: status_error(503) if table == "kb_knowledge" and fake.calls[-1][2] == 1000 else None
    store = wire_store(sc, fake, stored_record(_number(9)))

    await sc._sync_articles()

    assert _number(9) in store.records


@pytest.mark.asyncio
async def test_widened_kb_filter_adds_the_kb_and_its_articles(servicenow_connector) -> None:
    sc = servicenow_connector
    fake = FakeServiceNow({"kb_knowledge_base": [kb_row("kb1"), kb_row("kb2")], "kb_category": [], **_TWO_KBS})
    store = wire_store(sc, fake)
    _filters(sc, kbs=("in", ["kb1"]))
    await _sync_tree(sc)
    assert store.keys() == [_number(1)]

    _full_sync(sc)
    _filters(sc)  # the filter cleared
    sc.data_entities_processor.on_new_record_groups.reset_mock()
    await _sync_tree(sc)

    assert groups_written(sc) == ["kb1", "kb2"]
    assert store.keys() == [_number(1), _number(2)]


# --- indexing toggles and manual indexing ------------------------------------------------------


def _records(sc, indexing: dict) -> tuple:
    _filters(sc, indexing=indexing)
    article = sc._transform_to_article_webpage_record(TableAPIRecord(**article_row(1)))
    attachment = sc._transform_to_attachment_file_record(AttachmentMetadata(**attachment_row(1, "a0001")), article=article)
    return article, attachment


@pytest.mark.parametrize(("indexing", "article_off", "attachment_off"), [
    ({}, False, False),
    ({"knowledge_base": False}, True, False),
    ({"attachments": False}, False, True),
    ({"enable_manual_sync": True}, True, True),  # manual indexing: nothing is indexed until chosen
])
def test_indexing_toggles(servicenow_connector, indexing, article_off, attachment_off) -> None:
    article, attachment = _records(servicenow_connector, indexing)

    assert (article.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value) is article_off
    assert (attachment.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value) is attachment_off


def test_indexing_toggle_leaves_the_revision_alone(servicenow_connector) -> None:
    # The processor re-indexes a stored record only on a new revision, and keeps a COMPLETED one
    # COMPLETED, so toggling neither re-indexes unchanged records nor un-indexes indexed ones.
    on = _records(servicenow_connector, {})
    off = _records(servicenow_connector, {"knowledge_base": False, "attachments": False})

    assert [r.external_revision_id for r in on] == [r.external_revision_id for r in off]


@pytest.mark.asyncio
async def test_index_articles_off_still_syncs_with_grants(servicenow_connector) -> None:
    sc = servicenow_connector
    _filters(sc, indexing={"knowledge_base": False})
    store = wire_store(sc, FakeServiceNow({"kb_knowledge": [article_row(1)], "sys_attachment": [attachment_row(1, "a0001")]}),
                  stored_record(_number(1)))  # stored already: it is rewritten with the status too

    await sc._sync_articles()

    assert store.records[_number(1)].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
    assert store.records[file_key(1)].indexing_status != ProgressStatus.AUTO_INDEX_OFF.value


# --- filter options ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_options_page_1_has_more(servicenow_connector) -> None:
    sc = servicenow_connector
    wire_store(sc, FakeServiceNow({"kb_knowledge_base": [kb_row(f"kb{i:02d}") for i in range(25)] + [kb_row("old", active="false")]}))

    first = await sc.get_filter_options("knowledge_bases", page=1, limit=20)
    second = await sc.get_filter_options("knowledge_bases", page=2, limit=20)

    assert (first.success, len(first.options), first.has_more) == (True, 20, True)
    assert (len(second.options), second.has_more) == (5, False)
    assert [o.id for o in first.options + second.options] == [f"kb{i:02d}" for i in range(25)]


@pytest.mark.asyncio
async def test_options_search_matches_any_part_of_the_label(servicenow_connector) -> None:
    sc = servicenow_connector
    wire_store(sc, FakeServiceNow({"kb_knowledge_base": [kb_row("a", title="IT Support"), kb_row("b", title="HR"),
                                                    kb_row("c", title="Wiki^active=false")]}))

    found = await sc.get_filter_options("knowledge_bases", search="it")
    caret = await sc.get_filter_options("knowledge_bases", search="^active=")

    assert [o.label for o in found.options] == ["IT Support"]
    assert [o.id for o in caret.options] == ["c"]  # matched as text, never sent as a query


@pytest.mark.asyncio
async def test_category_options_are_labelled_with_kb_and_parents(servicenow_connector) -> None:
    sc = servicenow_connector
    wire_store(sc, FakeServiceNow({
        "kb_knowledge_base": [kb_row("kb1", title="IT"), kb_row("kb2", title="Old", active="false")],
        "kb_category": [category_row("c1", "kb1", label="Network"), category_row("c1a", "c1", label="VPN"),
                        category_row("c2", "kb2", label="Gone")],
    }))

    options = await sc.get_filter_options("folder_ids", search="vpn")
    every = await sc.get_filter_options("folder_ids")

    assert [(o.id, o.label) for o in options.options] == [("c1a", "IT › Network › VPN")]
    assert [o.id for o in every.options] == ["c1", "c1a"]  # nothing under an inactive knowledge base


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [status_error(403, "Insufficient rights: kb_knowledge_base"),
                                   ServiceNowRateLimited(reset_at=1_800_000_000)])
async def test_options_error_success_false(servicenow_connector, error) -> None:
    sc = servicenow_connector
    wire_store(sc, FakeServiceNow({}, fail=lambda table, query: error))

    response = await sc.get_filter_options("knowledge_bases")

    assert (response.success, response.options) == (False, [])
    assert "Could not load ServiceNow knowledge bases" in response.message
    assert ("Insufficient rights" in response.message) or ("rate limit" in response.message)


@pytest.mark.asyncio
async def test_options_unknown_key_raises(servicenow_connector) -> None:
    with pytest.raises(ValueError, match="Unsupported filter key"):
        await servicenow_connector.get_filter_options("space_keys")


# --- the filters are read, or the run stops ----------------------------------------------------


@pytest.mark.asyncio
async def test_filter_load_failure_fails_run_and_writes_nothing(servicenow_connector) -> None:
    sc = servicenow_connector
    sc.servicenow_client = MagicMock()
    sc.config_service.get_config = AsyncMock(side_effect=RuntimeError("store down"))
    wire_store(sc, FakeServiceNow(_TWO_KBS))

    with pytest.raises(RuntimeError, match="store down"):
        await sc.run_sync()

    assert sc.config_service.get_config.await_args.kwargs == {"raise_on_error": True}
    processor = sc.data_entities_processor
    for write in (processor.on_new_app_users, processor.on_new_record_groups, processor.on_new_records):
        write.assert_not_awaited()


@pytest.mark.asyncio
async def test_load_filters_reads_sync_and_indexing(servicenow_connector) -> None:
    sc = servicenow_connector
    sc.config_service.get_config = AsyncMock(return_value={"filters": {
        "sync": {"values": _sync_values(kbs=("not_in", ["kb2"]))},
        "indexing": {"values": {"attachments": {"operator": "is", "value": False, "type": "boolean"}}},
    }})

    await sc._load_filters()

    assert sc.sync_filters.get_value("knowledge_bases") == ["kb2"]
    assert not sc.indexing_filters.is_enabled("attachments")
