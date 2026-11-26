"""What the sweep after an Outlook full sync unlinks: nothing still in Microsoft 365, and
everything that is gone."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

from ms_graph_fakes import FakeCheckpointStore, MicrosoftCloudStub, graph_error, page
from test_outlook_sync_behaviour import (  # noqa: F401
    ANA,
    BO,
    CONNECTOR_ID,
    GROUP,
    INBOX,
    OutlookRecordsDb,
    api,
    connector,
    db,
    delta_path,
    delta_url,
    file_attachment,
    folder,
    fresh_notification_backoff,
    member,
    message,
    post,
    serve_delta,
    serve_group,
    set_sync_filters,
    thread,
)

from app.config.constants.arangodb import AppStatus, CollectionNames
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.connectors.core.sync.full_sync_coverage import FullSyncSeen
from app.connectors.core.sync.sync_runner import run_sync_task
from app.connectors.sources.microsoft.common.outlook_constants import (
    OutlookSyncPointKeys,
)

if TYPE_CHECKING:
    from app.connectors.sources.microsoft.outlook.connector import OutlookConnector


async def unlinked_by_full_sync(outlook: OutlookConnector, store: OutlookRecordsDb, checkpoints: FakeCheckpointStore) -> set[str]:
    """Run a full sync as the runner does (its sync points cleared first) and return the
    stored nodes, by external id, that the sweep after it unlinks."""
    checkpoints.sync_points.clear()
    stored = set(store.records) | set(store.record_groups) | set(store.user_groups)
    seen = FullSyncSeen()
    kept: set[str] = set()
    by_id = {r.id: k for k, r in store.records.items()}
    tracked = {
        "on_new_user_groups": (CollectionNames.GROUPS.value, lambda g: g.source_user_group_id),
        "on_new_record_groups": (CollectionNames.RECORD_GROUPS.value, lambda g: g.external_group_id),
        "on_new_records": (CollectionNames.RECORDS.value, lambda r: r.external_record_id),
    }

    def tracking(name: str) -> Any:  # noqa: ANN401
        collection, key = tracked[name]
        original = getattr(store, name)

        async def write(items: list[tuple[Any, Any]], **kwargs: object) -> None:
            for item, _ in items:
                seen.add(collection, key(item))
            await original(items, **kwargs)

        return write

    async def note_kept_record_group(connector_id: str, external_group_id: str) -> None:
        kept.add(external_group_id)

    async def note_kept_user_group(connector_id: str, external_group_id: str) -> None:
        kept.add(external_group_id)

    for name in tracked:
        setattr(store, name, tracking(name))
    store.begin_full_sync_run = lambda connector_id: seen
    store.end_full_sync_run = lambda connector_id, run: None
    store.note_kept_records = lambda connector_id, ids: kept.update(by_id.get(i, i) for i in ids)
    store.note_kept_record_group = note_kept_record_group
    store.note_kept_user_group = note_kept_user_group
    graph = AsyncMock()
    graph.get_document.return_value = {"id": CONNECTOR_ID, "status": AppStatus.FULL_SYNCING.value}
    graph.unlink_nodes_missed_by_full_sync.return_value = (0, True)
    try:
        await run_sync_task(outlook, CONNECTOR_ID, graph, logging.getLogger("test.full_sync"),
                            start_status=AppStatus.FULL_SYNCING.value)
    finally:
        for name in [*tracked, "begin_full_sync_run", "end_full_sync_run", "note_kept_records",
                     "note_kept_record_group", "note_kept_user_group"]:
            vars(store).pop(name, None)
    if not graph.unlink_nodes_missed_by_full_sync.await_count:
        return set()  # an incomplete run unlinks nothing
    written = set().union(*seen.by_collection.values())
    return stored - written - kept


def serve_bo(cloud: MicrosoftCloudStub, mails: list[dict[str, Any]]) -> None:
    """A second mailbox whose stored mail is the control: what it no longer lists is gone."""
    cloud.on("GET", "/v1.0/users/u-bo/mailFolders", page([folder("bo-inbox", "Inbox")]))
    serve_delta(cloud, {None: page(mails, delta_link=delta_url("B1", "u-bo", "bo-inbox"))},
                user="u-bo", folder_id="bo-inbox")


async def test_a_selected_folder_graph_answered_404_for_during_a_full_sync_keeps_its_links(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    api.on("GET", "/v1.0/users/u-ana/mailFolders", page([folder(INBOX, "Inbox"), folder("sent", "Sent Items")]))
    api.on("GET", "/v1.0/users/u-ana/mailFolders/inbox", folder(INBOX, "Inbox"))
    api.on("GET", "/v1.0/users/u-ana/mailFolders/sentitems", folder("sent", "Sent Items"))
    serve_delta(api, {None: page([message("m1", "Plan"), message("m-gone", "Old")], delta_link=delta_url("D1"))})
    api.on("GET", delta_path(folder_id="sent"), page([message("s1", "Sent")], delta_link=delta_url("S1", folder_id="sent")))
    set_sync_filters(connector, custom_folders={"operator": "is", "value": False, "type": "boolean"})
    await connector.run_sync()
    assert {"s1", "sent"} <= set(db.records) | set(db.record_groups)
    # Still listed among the top-level folders, but its well-known name answers 404.
    api.on("GET", "/v1.0/users/u-ana/mailFolders/sentitems", graph_error(404, "ErrorItemNotFound"))
    serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D2"))})

    unlinked = await unlinked_by_full_sync(connector, db, checkpoints)

    wrongly = unlinked & {"sent", "s1"}
    assert sorted(wrongly) == [], f"wrongly unlinked: {len(wrongly)}"
    assert unlinked & {"m-gone"} == {"m-gone"}, "really gone: still unlinked"


async def test_a_mail_attachment_listed_without_a_content_type_during_a_full_sync_keeps_its_links(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    serve_delta(api, {None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1"))})
    attachments = "/v1.0/users/u-ana/messages/m1/attachments"
    api.on("GET", attachments, page([file_attachment("a1"), file_attachment("a-gone", "old.pdf")]))
    await connector.run_sync()
    assert {"a1", "a-gone"} <= set(db.records)
    api.on("GET", attachments, page([{**file_attachment("a1"), "contentType": None}]))

    unlinked = await unlinked_by_full_sync(connector, db, checkpoints)

    wrongly = unlinked & {"a1"}
    assert sorted(wrongly) == [], f"wrongly unlinked: {len(wrongly)}"
    assert unlinked & {"a-gone"} == {"a-gone"}, "really gone: still unlinked"


async def test_a_new_attachment_listed_without_a_content_type_is_not_kept_or_written(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    serve_delta(api, {None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1"))})
    attachments = "/v1.0/users/u-ana/messages/m1/attachments"
    api.on("GET", attachments, page([file_attachment("a-gone", "old.pdf")]))
    await connector.run_sync()
    api.on("GET", attachments, page([{**file_attachment("a-new"), "contentType": None}]))

    unlinked = await unlinked_by_full_sync(connector, db, checkpoints)

    assert "a-new" not in db.records
    assert unlinked == {"a-gone"}, "the run is complete and its sweep still unlinks what is gone"


async def test_an_item_attachment_stored_by_an_older_version_is_still_unlinked(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    serve_delta(api, {None: page([message("m1", "Plan", attachments=True)], delta_link=delta_url("D1"))})
    attachments = "/v1.0/users/u-ana/messages/m1/attachments"
    api.on("GET", attachments, page([file_attachment("fwd", "Fwd: Plan")]))
    await connector.run_sync()
    assert "fwd" in db.records
    item = {**file_attachment("fwd", "Fwd: Plan"), "@odata.type": "#microsoft.graph.itemAttachment", "contentType": None}
    api.on("GET", attachments, page([item]))

    unlinked = await unlinked_by_full_sync(connector, db, checkpoints)

    assert "fwd" in unlinked, "an item attachment is never synced, so its old record is not kept"


async def test_a_group_post_attachment_listed_without_a_content_type_during_a_full_sync_keeps_its_links(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    serve_group(api, [member("u-ana", ANA)], threads=page([thread("t1", "Launch")]))
    api.on("GET", f"/v1.0/groups/{GROUP}/threads/t1/posts", page([{**post("p1"), "hasAttachments": True}]))
    post_path = f"/v1.0/groups/{GROUP}/threads/t1/posts/p1"
    api.on("GET", post_path, {**post("p1"), "attachments": [file_attachment("pa1"), file_attachment("pa-gone", "old.pdf")]})
    await connector.run_sync()
    assert {"pa1", "pa-gone"} <= set(db.records)
    api.on("GET", post_path, {**post("p1"), "attachments": [{**file_attachment("pa1"), "contentType": None}]})

    unlinked = await unlinked_by_full_sync(connector, db, checkpoints)

    wrongly = unlinked & {"pa1"}
    assert sorted(wrongly) == [], f"wrongly unlinked: {len(wrongly)}"
    assert unlinked & {"pa-gone"} == {"pa-gone"}, "really gone: still unlinked"


async def test_control_a_full_sync_with_nothing_skipped_unlinks_only_what_is_gone(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    serve_delta(api, {None: page([message("m1", "Plan"), message("m-gone", "Old")], delta_link=delta_url("D1"))})
    await connector.run_sync()
    serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D2"))})

    unlinked = await unlinked_by_full_sync(connector, db, checkpoints)

    assert unlinked == {"m-gone"}
    assert db.child_group_reads == [], "nothing was left out, so no stored folder is walked"


LISTING = "/v1.0/users/u-ana/mailFolders"
CUSTOM_FOLDERS_OFF = {"operator": "is", "value": False, "type": "boolean"}


def saved_folder_inventory(checkpoints: FakeCheckpointStore) -> list[str]:
    key = generate_record_sync_point_key("MAIL", OutlookSyncPointKeys.SEGMENT_FOLDER_INVENTORY, "u-ana")
    inventory = checkpoints.values_for(key)
    assert inventory is not None
    return inventory[OutlookSyncPointKeys.FOLDER_IDS]


def serve_sent(cloud: MicrosoftCloudStub, mails: list[dict[str, Any]], *, children: bool = False) -> None:
    cloud.on("GET", "/v1.0/users/u-ana/mailFolders/inbox", folder(INBOX, "Inbox"))
    cloud.on("GET", "/v1.0/users/u-ana/mailFolders/sentitems", folder("sent", "Sent Items"))
    cloud.on("GET", delta_path(folder_id="sent"), page(mails, delta_link=delta_url("S1", folder_id="sent")))
    if children:
        cloud.on("GET", "/v1.0/users/u-ana/mailFolders/sent/childFolders",
                 page([{**folder("sent-sub", "Q3"), "parentFolderId": "sent"}]))
        cloud.on("GET", delta_path(folder_id="sent-sub"),
                 page([message("q1", "Notes")], delta_link=delta_url("Q1", folder_id="sent-sub")))


async def test_a_404_on_a_full_sync_keeps_the_stored_subtree_of_a_listed_folder(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    api.on("GET", LISTING, page([folder(INBOX, "Inbox"), {**folder("sent", "Sent Items"), "childFolderCount": 1}]))
    serve_sent(api, [message("s1", "Sent")], children=True)
    serve_delta(api, {None: page([message("m1", "Plan"), message("m-gone", "Old")], delta_link=delta_url("D1"))})
    await connector.run_sync()
    assert {"sent-sub", "q1"} <= set(db.records) | set(db.record_groups)
    set_sync_filters(connector, custom_folders=CUSTOM_FOLDERS_OFF)
    api.on("GET", "/v1.0/users/u-ana/mailFolders/sentitems", graph_error(404, "ErrorItemNotFound"))
    serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D2"))})

    unlinked = await unlinked_by_full_sync(connector, db, checkpoints)

    assert unlinked == {"m-gone"}, "the run is complete and keeps Sent, its subfolder and their mail"
    assert "sent" in saved_folder_inventory(checkpoints)
    assert db.deleted_record_groups == []


async def test_a_404_on_a_full_sync_keeps_only_stored_folders_the_listing_left_out(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    api.on("GET", LISTING, page([folder(INBOX, "Inbox"), folder("sent", "Sent Items"), folder("old", "Old")]))
    serve_sent(api, [message("s1", "Sent")])
    api.on("GET", delta_path(folder_id="old"), page([message("o1", "Old mail")], delta_link=delta_url("O1", folder_id="old")))
    serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D1"))})
    await connector.run_sync()
    assert {"old", "o1"} <= set(db.records) | set(db.record_groups)
    set_sync_filters(connector, custom_folders=CUSTOM_FOLDERS_OFF)
    # Old was deleted; Custom was never synced.
    api.on("GET", LISTING, page([folder(INBOX, "Inbox"), folder("sent", "Sent Items"), folder("custom", "Custom")]))
    api.on("GET", "/v1.0/users/u-ana/mailFolders/sentitems", graph_error(404, "ErrorItemNotFound"))

    unlinked = await unlinked_by_full_sync(connector, db, checkpoints)

    assert unlinked == {"old", "o1"}
    assert "custom" not in db.record_groups
    assert sorted(saved_folder_inventory(checkpoints)) == [INBOX, "sent"]


async def test_a_full_sync_unlinks_a_deselected_folder_of_a_mailbox_without_an_archive(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    api.on("GET", LISTING, page([folder(INBOX, "Inbox"), folder("proj", "Projects")]))
    api.on("GET", delta_path(folder_id="proj"), page([message("p1", "Spec")], delta_link=delta_url("P1", folder_id="proj")))
    serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D1"))})
    await connector.run_sync()
    assert {"proj", "p1"} <= set(db.records) | set(db.record_groups)
    api.on("GET", f"{LISTING}/inbox", folder(INBOX, "Inbox"))
    for name in ("sentitems", "drafts", "junkemail", "outbox"):
        api.on("GET", f"{LISTING}/{name}", folder(f"wk-{name}", name))
    for name in ("archive", "conversationhistory"):
        api.on("GET", f"{LISTING}/{name}", graph_error(404, "ErrorItemNotFound"))
    set_sync_filters(connector, custom_folders=CUSTOM_FOLDERS_OFF)
    listed = page([message("m1", "Plan")], delta_link=delta_url("D1"))
    serve_delta(api, {None: listed, "D1": listed})

    for _ in range(2):
        unlinked = await unlinked_by_full_sync(connector, db, checkpoints)
        await connector.run_sync()

        assert unlinked >= {"proj", "p1"}, "a 404 for a folder the mailbox lacks keeps no deselected folder"
        assert saved_folder_inventory(checkpoints) == [INBOX]


async def test_a_full_sync_whose_only_selected_folder_answers_404_keeps_it(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    api.on("GET", "/v1.0/users/u-ana/mailFolders/inbox", folder(INBOX, "Inbox"))
    serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D1"))})
    set_sync_filters(
        connector,
        folders={"operator": "in", "value": ["Inbox"], "type": "multiselect"},
        custom_folders=CUSTOM_FOLDERS_OFF,
    )
    await connector.run_sync()
    assert {"mailbox:u-ana", INBOX, "m1"} <= set(db.records) | set(db.record_groups)
    api.on("GET", "/v1.0/users/u-ana/mailFolders/inbox", graph_error(404, "ErrorItemNotFound"))
    serve_delta(api, {None: page([], delta_link=delta_url("D2"))})
    db.add_mailbox_user(BO, "pipeshub-bo")
    serve_bo(api, [])
    api.on("GET", "/v1.0/users/u-bo/mailFolders/inbox", folder("bo-inbox", "Inbox"))
    serve_delta(api, {None: page([message("b-gone", "Old", to=ANA)], delta_link=delta_url("B1", "u-bo", "bo-inbox"))},
                user="u-bo", folder_id="bo-inbox")
    await connector.run_sync()
    serve_bo(api, [])

    unlinked = await unlinked_by_full_sync(connector, db, checkpoints)

    assert unlinked == {"b-gone"}, "the run is complete and keeps ana's mailbox, Inbox and m1"
    assert INBOX not in db.deleted_record_groups


async def test_a_404_on_a_full_sync_whose_store_walk_fails_unlinks_nothing(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    api.on("GET", LISTING, page([folder(INBOX, "Inbox"), folder("sent", "Sent Items")]))
    serve_sent(api, [message("s1", "Sent")])
    serve_delta(api, {None: page([message("m1", "Plan"), message("m-gone", "Old")], delta_link=delta_url("D1"))})
    set_sync_filters(connector, custom_folders=CUSTOM_FOLDERS_OFF)
    await connector.run_sync()
    api.on("GET", "/v1.0/users/u-ana/mailFolders/sentitems", graph_error(404, "ErrorItemNotFound"))
    serve_delta(api, {None: page([message("m1", "Plan")], delta_link=delta_url("D2"))})
    db.fail_child_group_reads = True

    unlinked = await unlinked_by_full_sync(connector, db, checkpoints)

    assert unlinked == set(), "an incomplete run unlinks nothing"
    assert connector.full_sync_coverage().complete is False
    assert "sent" in saved_folder_inventory(checkpoints)
    assert {"sent", "s1"} <= set(db.records) | set(db.record_groups)


async def test_a_folder_kept_by_a_full_sync_is_deleted_once_it_leaves_the_listing(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    api.on("GET", LISTING, page([folder(INBOX, "Inbox"), folder("sent", "Sent Items")]))
    serve_sent(api, [message("s1", "Sent")])
    set_sync_filters(connector, custom_folders=CUSTOM_FOLDERS_OFF)
    await connector.run_sync()
    api.on("GET", "/v1.0/users/u-ana/mailFolders/sentitems", graph_error(404, "ErrorItemNotFound"))
    await unlinked_by_full_sync(connector, db, checkpoints)
    assert "s1" in db.records

    api.on("GET", LISTING, page([folder(INBOX, "Inbox")]))
    await connector.run_sync()

    assert "sent" in db.deleted_record_groups
    assert "s1" not in db.records


async def test_an_incremental_run_with_a_404_walks_no_stored_folder(
    connector, api, db, checkpoints,  # noqa: F811
) -> None:
    api.on("GET", LISTING, page([folder(INBOX, "Inbox"), folder("sent", "Sent Items")]))
    serve_sent(api, [message("s1", "Sent")])
    set_sync_filters(connector, custom_folders=CUSTOM_FOLDERS_OFF)
    await connector.run_sync()
    api.on("GET", "/v1.0/users/u-ana/mailFolders/sentitems", graph_error(404, "ErrorItemNotFound"))

    await connector.run_sync()

    assert db.child_group_reads == []
    assert db.kept_records == [] and db.kept_record_groups == []
    assert "s1" in db.records
