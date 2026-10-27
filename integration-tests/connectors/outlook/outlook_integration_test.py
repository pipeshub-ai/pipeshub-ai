# pyright: ignore-file

"""Outlook (team / app-only) connector — integration tests.

The suite is built around as few syncs as possible: a sync is the slow part (minutes), an
assertion is milliseconds. ``conftest.py`` seeds a whole world through Graph before the connector
exists, and each phase below applies one batch of source changes followed by ONE sync, then
asserts every case of that batch.

  B0  baseline full sync      properties, data shapes, paging, threading, permissions, access
  --  no sync                 stream (as the owner) and reindex
  B1  incremental sync        adds / updates / moves / deletes, membership added, users+groups added
  B2  incremental sync        membership removed, group+user deleted, idempotency of a no-op run
  F1  full sync (filters)     folders=[Inbox, Sent Items], custom off, every indexing toggle off
  F1b full sync (filters)     custom off, no standard folder selected
  F2  full sync (filters)     users=[S] (upper-cased), both groups excluded
  F3  full sync (filters)     folders=[Inbox] + custom on, users not_in [S]
  F4  full sync (filters)     folders not_in [Inbox] + custom on
  R   full sync (restore)     baseline scope + indexing back on
  FS  forced full sync        the UI's "full sync" changes no record

Ten syncs in total. Access is asserted two ways: the product's batch access check
(``check_access``, what search/chat/listing use) and the record endpoint as each mailbox owner.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio
import requests

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.constants.arangodb import CollectionNames, ProgressStatus  # noqa: E402
from app.connectors.sources.microsoft.common.outlook_constants import (  # noqa: E402
    OutlookDefaults,
    OutlookSyncConfig,
    mail_content_revision,
)
from app.models.entities import RecordType  # noqa: E402
from app.models.permission import EntityType, PermissionType  # noqa: E402
from connectors.outlook import constants as C  # noqa: E402
from connectors.outlook.constants import OUTLOOK_CONNECTOR_TYPE  # noqa: E402
from connectors.outlook.conftest import (  # noqa: E402
    OutlookWorld,
    baseline_sync_values,
    poll_message,
    retire_user,
    seed_group_post,
    set_membership,
)
from connectors.outlook.outlook_expected import (  # noqa: E402
    FILE_SKIP_COMPARE,
    MAIL_SKIP_COMPARE,
    RECORD_GROUP_SKIP_COMPARE,
    OutlookExpected,
    mailbox_external_id,
)
from connectors.outlook.outlook_test_utils import (  # noqa: E402
    admitted,
    bool_value,
    list_value,
    run_sync_and_wait,
    sync_filters,
    wait_until_outlook_condition,
)
from helper.record_access import (  # noqa: E402
    record_access_status,
    wait_for_record_access,
)
from helper.second_user import NO_ACCESS_STATUSES, SecondUser  # noqa: E402
from validation.graph_entity_validator import (  # noqa: E402
    assert_graph_entity_matches,
    assert_graph_entity_with_edges,
)

if TYPE_CHECKING:
    from connectors.outlook.outlook_source_helper import OutlookSourceHelper
    from helper.assertions import ConnectorAssertions
    from helper.graph_provider import GraphProviderProtocol
    from pipeshub_client import PipeshubClient

logger = logging.getLogger("outlook-integration-test")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.outlook,
    pytest.mark.asyncio(loop_scope="session"),
]

_PERMISSION = CollectionNames.PERMISSION.value
_INHERIT = CollectionNames.INHERIT_PERMISSIONS.value
_BELONGS_TO = CollectionNames.BELONGS_TO.value
_USERS = CollectionNames.USERS.value
_GROUPS = CollectionNames.GROUPS.value
_RECORDS = CollectionNames.RECORDS.value
_RECORD_GROUPS = CollectionNames.RECORD_GROUPS.value
_APPS = CollectionNames.APPS.value
_AUTO_INDEX_OFF = ProgressStatus.AUTO_INDEX_OFF.value


# --------------------------------------------------------------------------- shared helpers


class Ctx:
    """Everything a test needs, so test signatures stay short."""

    def __init__(
        self,
        world: OutlookWorld,
        connector: dict[str, Any],
        client: PipeshubClient,
        graph: GraphProviderProtocol,
        helper: OutlookSourceHelper,
        assertions: ConnectorAssertions,
    ) -> None:
        self.world = world
        self.cid: str = connector["connector_id"]
        self.connector = connector
        self.client = client
        self.graph = graph
        self.helper = helper
        self.assertions = assertions
        self.org_id: str = client.org_id
        self.p = world.primary
        self.s = world.secondary

    # -- graph lookups --------------------------------------------------

    async def record(self, external_id: str) -> Any:
        return await self.graph.get_record_by_external_id(self.cid, external_id)

    async def must_record(self, external_id: str, what: str) -> Any:
        rec = await self.record(external_id)
        assert rec is not None, f"{what} ({external_id}) was not synced"
        return rec

    async def group(self, external_id: str) -> Any:
        return await self.graph.get_record_group_by_external_id(self.cid, external_id)

    async def must_group(self, external_id: str, what: str) -> Any:
        rg = await self.group(external_id)
        assert rg is not None, f"{what} ({external_id}) was not synced as a RecordGroup"
        return rg

    async def mailbox(self, source_id: str) -> Any:
        return await self.must_group(mailbox_external_id(source_id), f"mailbox of {source_id}")

    async def edges(self, from_coll: str, from_key: str, to_coll: str, to_key: str, edge: str) -> list[dict[str, Any]]:
        return await self.graph.find_edges_between(from_coll, from_key, to_coll, to_key, edge)

    async def folder_id(self, user_source_id: str, well_known: str) -> str:
        folder = await self.helper.get_folder(user_source_id, well_known)
        assert folder is not None, f"well-known folder {well_known} missing for {user_source_id}"
        return folder.id

    # -- access ---------------------------------------------------------

    async def admitted(self, user_graph_id: str, *node_ids: str) -> frozenset[str]:
        return await admitted(self.graph, user_graph_id, self.org_id, list(node_ids))

    async def assert_access(self, user: Any, allowed: dict[str, str], denied: dict[str, str]) -> None:
        """``allowed``/``denied`` map a description to a node id; checked in one batch call."""
        got = await self.admitted(user.graph_id, *allowed.values(), *denied.values())
        missing = [f"{d} ({i})" for d, i in allowed.items() if i not in got]
        leaked = [f"{d} ({i})" for d, i in denied.items() if i in got]
        assert not missing, f"{user.email} cannot read what it owns: {missing}"
        assert not leaked, f"{user.email} can read what it must not: {leaked}"

    # -- sync -----------------------------------------------------------

    async def incremental_sync(self, description: str) -> int:
        def _trigger() -> None:
            self.client.toggle_sync(self.cid, enable=False)
            self.client.wait(3)
            self.client.toggle_sync(self.cid, enable=True)

        return await run_sync_and_wait(self.client, self.graph, self.cid, _trigger, description=description)

    async def filter_sync(self, description: str, sync_values: dict[str, Any], indexing: dict[str, Any]) -> int:
        """Change filters (which forces a full sync) and wait for that sync."""
        def _trigger() -> None:
            self.client.update_connector_filters_sync_safe(
                self.cid,
                filters={"sync": sync_filters(**sync_values)["sync"], "indexing": {"values": indexing}},
            )

        return await run_sync_and_wait(self.client, self.graph, self.cid, _trigger, description=description)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def ctx(
    outlook_world: OutlookWorld,
    outlook_connector: dict[str, Any],
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    outlook_source_helper: OutlookSourceHelper,
    connector_assertions: ConnectorAssertions,
) -> Ctx:
    return Ctx(outlook_world, outlook_connector, pipeshub_client, graph_provider,
               outlook_source_helper, connector_assertions)


def _stream_as(ctx: Ctx, user: SecondUser, record_id: str) -> requests.Response:
    """Stream a record with ``user``'s own session (the owner check is part of the contract)."""
    return requests.get(
        f"{ctx.client.base_url}{ctx.client._stream_record_path(record_id)}",
        headers=user.headers, timeout=120,
    )


def _reindex_as(ctx: Ctx, user: SecondUser, record_id: str) -> None:
    """Reindex as ``user``: the endpoint requires access to the record, which only its owner has."""
    resp = requests.post(
        f"{ctx.client.base_url}{ctx.client._reindex_record_path(record_id)}", headers=user.headers, timeout=60,
    )
    assert resp.status_code == 200, f"reindex of {record_id} failed: HTTP {resp.status_code} {resp.text[:300]}"


async def _await_condition(check: Any, description: str, timeout: int = 120) -> None:
    await wait_until_outlook_condition(check, description=description, timeout=timeout, poll_interval=3)


async def _index_mark(ctx: Ctx, record_id: str) -> tuple[Any, Any]:
    """What a reindex moves: the indexer stamps lastIndexTimestamp when it finishes a record."""
    doc = await ctx.graph.get_document(record_id, _RECORDS) or {}
    return doc.get("lastIndexTimestamp"), doc.get("indexingStatus")


async def _reindex_and_wait(ctx: Ctx, user: SecondUser, record_ids: list[str]) -> None:
    """Reindex each record and wait until the indexer has visibly picked every one up again."""
    before = {rid: await _index_mark(ctx, rid) for rid in record_ids}
    for rid in record_ids:
        _reindex_as(ctx, user, rid)

    async def _reindexed() -> bool:
        for rid, (stamp, status) in before.items():
            now_stamp, now_status = await _index_mark(ctx, rid)
            if (now_stamp or 0) <= (stamp or 0) and now_status == status:
                return False
        return True

    await _await_condition(_reindexed, f"reindex picked up {record_ids}", timeout=C.REINDEX_TIMEOUT)


# =============================================================================
# B0 — baseline full sync
# =============================================================================


class TestB0Baseline:
    @pytest.mark.order(1)
    async def test_tc_ol_sync_001_baseline_full_sync(self, ctx: Ctx) -> None:
        """Full sync writes both seed users, their mailbox roots, the seed group and records."""
        assert ctx.connector["baseline_count"] > 0, "baseline sync produced no records"
        for mailbox in (ctx.p, ctx.s):
            user = await ctx.graph.get_user_by_source_id(mailbox.source_id, ctx.cid)
            assert user is not None, f"{mailbox.email} not synced as an app user"
            await ctx.mailbox(mailbox.source_id)
        assert await ctx.graph.get_user_group_by_external_id(ctx.cid, ctx.world.group_id) is not None

    @pytest.mark.order(2)
    async def test_tc_ol_user_001_user_properties(self, ctx: Ctx) -> None:
        """App user: AAD object id, email, display name, linked to the connector."""
        user = await ctx.assertions.assert_user_exists(
            ctx.cid, source_user_id=ctx.p.source_id, email=ctx.p.email,
        )
        assert user.full_name, "user should have a full_name"
        assert await ctx.graph.count_user_app_relation_edges(ctx.cid) >= 2

    @pytest.mark.order(3)
    async def test_tc_ol_group_001_group_properties_and_members(self, ctx: Ctx) -> None:
        """M365 group → AppUserGroup with exactly the group's user members as member edges."""
        group = await ctx.helper.get_group(ctx.world.group_id)
        actual = await ctx.graph.get_user_group_by_external_id(ctx.cid, ctx.world.group_id)
        assert actual is not None
        await assert_graph_entity_with_edges(
            OutlookExpected.app_user_group(group, connector_id=ctx.cid), actual,
            entity="app_user_group", connector_id=ctx.cid, graph_provider=ctx.graph,
            skip_compare=frozenset({"created_at", "updated_at", "source_created_at", "source_updated_at", "description"}),
        )
        members = await ctx.helper.list_group_members(ctx.world.group_id)
        user_members = [m for m in members if getattr(m, "user_principal_name", None) or getattr(m, "mail", None)]
        assert await ctx.graph.count_group_members(ctx.cid, ctx.world.group_id) == len(user_members), (
            "member edges must mirror the group's user members"
        )
        user_node = await ctx.graph.get_user_by_source_id(ctx.p.source_id, ctx.cid)
        assert await ctx.edges(_USERS, user_node.id, _GROUPS, actual.id, _PERMISSION), "P is a member of the seed group"
        # groups filter `in`: every other group in the tenant stays out.
        others = [g for g in await ctx.helper.list_unified_groups() if g.id not in (ctx.world.group_id,)]
        leaked = [g.id for g in others if await ctx.graph.get_user_group_by_external_id(ctx.cid, g.id) is not None]
        assert not leaked, f"groups outside the `in` filter were synced: {leaked}"

    @pytest.mark.order(4)
    async def test_tc_ol_gmbx_001_group_mailbox(self, ctx: Ctx) -> None:
        """Group mailbox RecordGroup: READ grant from the group, no path from the app."""
        group = await ctx.helper.get_group(ctx.world.group_id)
        rg = await ctx.must_group(ctx.world.group_id, "seed group mailbox")
        await assert_graph_entity_with_edges(
            OutlookExpected.record_group_group_mailbox(group, connector_id=ctx.cid), rg,
            entity="record_group", connector_id=ctx.cid, graph_provider=ctx.graph,
            skip_compare=RECORD_GROUP_SKIP_COMPARE,
        )
        group_node = await ctx.graph.get_user_group_by_external_id(ctx.cid, ctx.world.group_id)
        await ctx.assertions.assert_permission_role(
            from_collection=_GROUPS, from_key=group_node.id, to_collection=_RECORD_GROUPS, to_key=rg.id,
            expected_role=PermissionType.READ.value, expected_type=EntityType.GROUP.value,
        )
        assert not await ctx.edges(_RECORD_GROUPS, rg.id, _APPS, ctx.cid, _INHERIT), (
            "a group mailbox must not inherit from the app: that opens it to everyone on the connector"
        )

    @pytest.mark.order(5)
    async def test_tc_ol_mbx_001_user_mailbox_root(self, ctx: Ctx) -> None:
        """Each user mailbox has one root RecordGroup carrying the only OWNER grant."""
        for mailbox in (ctx.p, ctx.s):
            rg = await ctx.mailbox(mailbox.source_id)
            assert_graph_entity_matches(
                OutlookExpected.record_group_mailbox(
                    connector_id=ctx.cid, user_email=mailbox.email, user_source_id=mailbox.source_id,
                ),
                rg, entity="record_group", skip_compare=RECORD_GROUP_SKIP_COMPARE,
            )
            user_node = await ctx.graph.get_user_by_source_id(mailbox.source_id, ctx.cid)
            await ctx.assertions.assert_permission_role(
                from_collection=_USERS, from_key=user_node.id, to_collection=_RECORD_GROUPS, to_key=rg.id,
                expected_role=PermissionType.OWNER.value, expected_type=EntityType.USER.value,
            )
            assert not await ctx.edges(_RECORD_GROUPS, rg.id, _APPS, ctx.cid, _INHERIT), (
                f"{mailbox.email}'s mailbox must not inherit from the app"
            )

    @pytest.mark.order(6)
    async def test_tc_ol_fold_001_folder_hierarchy(self, ctx: Ctx) -> None:
        """Top-level folder hangs under the mailbox, a child under its parent; no folder has its own grant."""
        f = ctx.world.folders
        mailbox = await ctx.mailbox(ctx.p.source_id)
        user_node = await ctx.graph.get_user_by_source_id(ctx.p.source_id, ctx.cid)
        root_src = await ctx.helper.get_folder(ctx.p.source_id, f["root"])
        props_src = await ctx.helper.get_folder(ctx.p.source_id, f["props"])
        for label, src, top, parent_rg in (("root", root_src, True, mailbox), ("props", props_src, False, None)):
            rg = await ctx.must_group(f[label], f"folder {label}")
            assert_graph_entity_matches(
                OutlookExpected.record_group_folder(
                    src, connector_id=ctx.cid, user_email=ctx.p.email,
                    user_source_id=ctx.p.source_id, is_top_level=top,
                ),
                rg, entity="record_group", skip_compare=RECORD_GROUP_SKIP_COMPARE,
            )
            parent = parent_rg or await ctx.must_group(f["root"], "root folder")
            assert await ctx.edges(_RECORD_GROUPS, rg.id, _RECORD_GROUPS, parent.id, _INHERIT), (
                f"folder {label} must inherit from its parent"
            )
            assert await ctx.edges(_RECORD_GROUPS, rg.id, _RECORD_GROUPS, parent.id, _BELONGS_TO), (
                f"folder {label} must belong to its parent"
            )
            assert not await ctx.edges(_USERS, user_node.id, _RECORD_GROUPS, rg.id, _PERMISSION), (
                f"folder {label} must not carry its own grant (the mailbox holds it)"
            )
            assert not await ctx.edges(_RECORD_GROUPS, rg.id, _APPS, ctx.cid, _BELONGS_TO), (
                f"nested folder {label} must not also hang off the app"
            )

    @pytest.mark.order(7)
    async def test_tc_ol_fold_002_deep_nesting(self, ctx: Ctx) -> None:
        """Three levels of subfolders keep their parent chain; mail at the bottom is synced and readable."""
        f = ctx.world.folders
        for child, parent in (("deep1", "root"), ("deep2", "deep1"), ("deep3", "deep2")):
            rg = await ctx.must_group(f[child], f"folder {child}")
            assert rg.parent_external_group_id == f[parent], f"{child} must hang under {parent}"
        rec = await ctx.must_record(ctx.world.messages["deep"], "mail in Deep3")
        assert rec.external_record_group_id == f["deep3"]
        await ctx.assert_access(ctx.p, {"mail in Deep3": rec.id}, {})

    @pytest.mark.order(8)
    async def test_tc_ol_fold_003_child_folder_paging(self, ctx: Ctx) -> None:
        """A folder with more children than one Graph page (10) syncs every child."""
        f = ctx.world.folders
        missing = [
            label for label in (f"wide{i:02d}" for i in range(C.WIDE_CHILD_FOLDERS))
            if await ctx.group(f[label]) is None
        ]
        assert not missing, f"child folders dropped across Graph pages: {missing}"
        await ctx.must_record(ctx.world.messages["wide_last"], "mail in the last child folder")

    @pytest.mark.order(8)
    async def test_tc_ol_page_001_message_paging(self, ctx: Ctx) -> None:
        """A folder with more messages than one delta page syncs every message (nextLink followed)."""
        ids = ctx.world.bulk_message_ids
        assert ids, "bulk folder was not seeded"
        missing = [mid for mid in ids if await ctx.record(mid) is None]
        assert not missing, f"{len(missing)} of {len(ids)} messages dropped across delta pages"

    @pytest.mark.order(9)
    async def test_tc_ol_mail_001_mail_properties(self, ctx: Ctx) -> None:
        """Mail: subject (unicode/emoji), from/to/cc/bcc, thread ids, content revision; inherits its folder."""
        m, f = ctx.world.messages, ctx.world.folders
        src = await ctx.helper.get_message(ctx.p.source_id, m["basic"])
        rec = await ctx.graph.get_typed_record_by_external_id(ctx.cid, m["basic"])
        assert rec is not None, "basic mail was not synced"
        await assert_graph_entity_with_edges(
            OutlookExpected.mail_record(src, connector_id=ctx.cid, folder_id=f["props"]), rec,
            entity="mail_record", connector_id=ctx.cid, graph_provider=ctx.graph,
            skip_compare=MAIL_SKIP_COMPARE,
        )
        assert rec.record_name == ctx.world.subjects["basic"], "unicode/emoji subject must round-trip exactly"
        assert rec.external_revision_id == mail_content_revision(src), (
            "external_revision_id must be the mail's content revision (it drives re-index on edit)"
        )
        assert rec.to_emails == [ctx.s.email]
        assert rec.cc_emails == [ctx.p.email]
        assert rec.bcc_emails == [C.EXTERNAL_ADDRESS]
        props = await ctx.must_group(f["props"], "Props folder")
        assert await ctx.edges(_RECORDS, rec.id, _RECORD_GROUPS, props.id, _INHERIT), "mail must inherit its folder"
        for mailbox in (ctx.p, ctx.s):
            user_node = await ctx.graph.get_user_by_source_id(mailbox.source_id, ctx.cid)
            assert not await ctx.edges(_USERS, user_node.id, _RECORDS, rec.id, _PERMISSION), (
                f"mail must not carry a direct grant to {mailbox.email} (recipients are not an audience)"
            )

    @pytest.mark.order(10)
    async def test_tc_ol_mail_002_empty_subject_and_body(self, ctx: Ctx) -> None:
        """A mail with no subject and no body syncs under the default subject."""
        rec = await ctx.must_record(ctx.world.messages["empty"], "empty mail")
        assert rec.record_name == OutlookDefaults.SUBJECT

    @pytest.mark.order(11)
    async def test_tc_ol_filter_received_date_baseline(self, ctx: Ctx) -> None:
        """received_date is_after: mail delivered before the cutoff is not synced, mail after it is."""
        assert await ctx.record(ctx.world.messages["old"]) is None, "mail older than the cutoff was synced"
        await ctx.must_record(ctx.world.messages["basic"], "mail after the cutoff")
        # The seed group's older conversations (from before this run) fall outside the window too.
        cutoff = ctx.world.started_at - timedelta(minutes=C.RECEIVED_CUTOFF_SLACK_MINUTES)
        older = [t for t in await ctx.helper.list_group_threads(ctx.world.group_id)
                 if t.lastDeliveredDateTime and datetime.fromisoformat(t.lastDeliveredDateTime.replace("Z", "+00:00")) < cutoff]
        for t in older[:3]:
            for p in await ctx.helper.list_thread_posts(ctx.world.group_id, t.id):
                assert await ctx.record(p.id) is None, f"group post {p.id} older than the cutoff was synced"

    @pytest.mark.order(11)
    async def test_tc_ol_fold_004_deleted_items_not_synced(self, ctx: Ctx) -> None:
        """Mail already in Deleted Items at the first sync is not a record; the folder is no RecordGroup."""
        assert await ctx.record(ctx.world.messages["in_deleted_items"]) is None, "Deleted Items mail was synced"
        assert await ctx.group(await ctx.folder_id(ctx.p.source_id, "deleteditems")) is None

    @pytest.mark.order(12)
    async def test_tc_ol_att_001_attachment_properties(self, ctx: Ctx) -> None:
        """Attachment → FileRecord child of its mail, inheriting the mail (not the folder)."""
        m, a, f = ctx.world.messages, ctx.world.attachments, ctx.world.folders
        rec = await ctx.graph.get_typed_record_by_external_id(ctx.cid, a["txt"])
        assert rec is not None, "text attachment was not synced"
        src = next(x for x in await ctx.helper.list_attachments(ctx.p.source_id, m["att"]) if x.id == a["txt"])
        await assert_graph_entity_with_edges(
            OutlookExpected.file_record(src, connector_id=ctx.cid, parent_message_id=m["att"], folder_id=f["props"]),
            rec, entity="file_record", connector_id=ctx.cid, graph_provider=ctx.graph,
            skip_compare=FILE_SKIP_COMPARE,
        )
        mail = await ctx.must_record(m["att"], "mail with attachments")
        assert await ctx.edges(_RECORDS, rec.id, _RECORDS, mail.id, _INHERIT), "attachment must inherit its mail"
        assert a["txt"] in await ctx.graph.get_record_outgoing_relations(ctx.cid, m["att"], "ATTACHMENT")

    @pytest.mark.order(13)
    async def test_tc_ol_att_002_many_attachments(self, ctx: Ctx) -> None:
        """Every attachment of a mail with many attachments is synced."""
        a = ctx.world.attachments
        missing = [k for k in (f"many{i:02d}" for i in range(C.MANY_ATTACHMENTS)) if await ctx.record(a[k]) is None]
        assert not missing, f"attachments dropped: {missing}"

    @pytest.mark.order(14)
    async def test_tc_ol_att_004_attachment_names(self, ctx: Ctx) -> None:
        """Unicode, extension-less and duplicate attachment names: one record each, name kept exactly."""
        a = ctx.world.attachments
        ids = set()
        for label, name in C.ODD_ATTACHMENT_NAMES.items():
            rec = await ctx.must_record(a[label], f"attachment {name!r}")
            assert rec.record_name == name, f"{label}: stored {rec.record_name!r}, source {name!r}"
            ids.add(rec.id)
        assert len(ids) == len(C.ODD_ATTACHMENT_NAMES), "same-named attachments collapsed into one record"
        # A name without an extension keeps its name; the extension comes from the mime type.
        noext = await ctx.graph.get_typed_record_by_external_id(ctx.cid, a["name_noext"])
        assert noext.extension == "txt", f"text/plain attachment without an extension: {noext.extension!r}"

    @pytest.mark.order(14)
    async def test_tc_ol_att_003_inline_image(self, ctx: Ctx) -> None:
        """An inline (cid:) image is an attachment record with its image mime type."""
        rec = await ctx.must_record(ctx.world.attachments["inline"], "inline image")
        assert rec.mime_type == "image/png"

    @pytest.mark.order(15)
    async def test_tc_ol_xchg_001_same_message_two_mailboxes(self, ctx: Ctx) -> None:
        """One P→S message: a record per mailbox, same internetMessageId, each under its owner's folder."""
        m = ctx.world.messages
        sent = await ctx.must_record(m["p_sent"], "P's Sent Items copy")
        inbox = await ctx.must_record(m["s_copy"], "S's Inbox copy")
        assert sent.id != inbox.id
        typed_sent = await ctx.graph.get_typed_record_by_external_id(ctx.cid, m["p_sent"])
        typed_inbox = await ctx.graph.get_typed_record_by_external_id(ctx.cid, m["s_copy"])
        assert typed_sent.internet_message_id and typed_sent.internet_message_id == typed_inbox.internet_message_id
        # The revision is a content hash, not changeKey, so a flag or category never re-indexes it.
        for rec, mailbox, ext in ((sent, ctx.p, m["p_sent"]), (inbox, ctx.s, m["s_copy"])):
            assert rec.external_revision_id == mail_content_revision(await ctx.helper.get_message(mailbox.source_id, ext))
        assert sent.external_record_group_id == await ctx.folder_id(ctx.p.source_id, "sentitems")
        assert inbox.external_record_group_id == await ctx.folder_id(ctx.s.source_id, "inbox")
        att = await ctx.helper.list_attachments(ctx.s.source_id, m["s_copy"])
        assert att, "S's copy should carry the attachment"
        await ctx.must_record(att[0].id, "attachment on S's copy")

    @pytest.mark.order(16)
    async def test_tc_ol_thread_001_reply_links_to_parent(self, ctx: Ctx) -> None:
        """A reply is linked to the message it answers (conversationIndex parent) within one mailbox."""
        m = ctx.world.messages
        await ctx.must_record(m["p_reply"], "reply in P's Inbox")
        parents = await ctx.graph.get_record_incoming_relations(ctx.cid, m["p_reply"], "SIBLING")
        assert parents == [m["p_sent"]], f"reply must have exactly one thread parent (P's sent copy), got {parents}"

    @pytest.mark.order(17)
    async def test_tc_ol_gmail_001_group_post(self, ctx: Ctx) -> None:
        """Group conversation post → GROUP_MAIL inheriting the group mailbox; its attachment inherits the post."""
        w = ctx.world
        rec = await ctx.must_record(w.group_posts["group"], "group post")
        assert rec.record_type == RecordType.GROUP_MAIL
        assert rec.record_name == w.subjects["group"]
        assert rec.external_record_group_id == w.group_id
        typed = await ctx.graph.get_typed_record_by_external_id(ctx.cid, w.group_posts["group"])
        assert typed.thread_id == w.group_threads["group"]
        assert typed.from_email.lower() == ctx.p.email.lower()
        gmbx = await ctx.must_group(w.group_id, "seed group mailbox")
        assert await ctx.edges(_RECORDS, rec.id, _RECORD_GROUPS, gmbx.id, _INHERIT), "post must inherit the group mailbox"
        att = await ctx.must_record(w.group_attachments["group"], "group post attachment")
        assert att.parent_external_record_id == w.group_posts["group"]
        assert await ctx.edges(_RECORDS, att.id, _RECORDS, rec.id, _INHERIT), "post attachment must inherit the post"

    @pytest.mark.order(18)
    async def test_tc_ol_perm_001_access_matrix(self, ctx: Ctx) -> None:
        """Owner reads its whole mailbox; the other mailbox owner reads none of it; groups by membership."""
        w, m, a, f = ctx.world, ctx.world.messages, ctx.world.attachments, ctx.world.folders
        ids = {}
        for label, ext in (("basic", m["basic"]), ("att txt", a["txt"]), ("P sent", m["p_sent"]),
                           ("P reply", m["p_reply"]), ("S copy", m["s_copy"]), ("deep", m["deep"]),
                           ("group post", w.group_posts["group"]), ("group att", w.group_attachments["group"])):
            ids[label] = (await ctx.must_record(ext, label)).id
        ids["root folder"] = (await ctx.must_group(f["root"], "root")).id
        ids["P mailbox"] = (await ctx.mailbox(ctx.p.source_id)).id
        ids["S mailbox"] = (await ctx.mailbox(ctx.s.source_id)).id
        ids["group mailbox"] = (await ctx.must_group(w.group_id, "group mailbox")).id
        p_own = ("basic", "att txt", "P sent", "P reply", "deep", "root folder", "P mailbox")
        groupish = ("group post", "group att", "group mailbox")
        await ctx.assert_access(
            ctx.p,
            {k: ids[k] for k in p_own + groupish},
            {k: ids[k] for k in ("S copy", "S mailbox")},
        )
        await ctx.assert_access(
            ctx.s,
            {k: ids[k] for k in ("S copy", "S mailbox")},
            {k: ids[k] for k in p_own + groupish},
        )
        # The org admin running the suite is not in the tenant: admin rights are not mail access.
        admin_email = os.getenv("PIPESHUB_TEST_USER_EMAIL")
        if not admin_email:  # a run that signs in with an OAuth client only
            return
        admin = await ctx.graph.graph_find_user_by_email(admin_email)
        assert admin is not None
        admin_id = str(admin.get("id") or admin.get("_key"))
        if admin_id not in (ctx.p.graph_id, ctx.s.graph_id):
            got = await ctx.admitted(admin_id, *ids.values())
            assert not got, f"org admin outside the tenant can read mailbox content: {sorted(got)}"

    @pytest.mark.order(19)
    async def test_tc_ol_perm_002_record_endpoint_as_owner(self, ctx: Ctx) -> None:
        """End to end: each owner opens its own mail; the other gets a denial (never a 5xx)."""
        m, w = ctx.world.messages, ctx.world
        basic = (await ctx.must_record(m["basic"], "basic")).id
        s_copy = (await ctx.must_record(m["s_copy"], "S copy")).id
        post = (await ctx.must_record(w.group_posts["group"], "group post")).id
        assert record_access_status(ctx.p.session, basic) == 200
        assert record_access_status(ctx.s.session, basic) in NO_ACCESS_STATUSES
        assert record_access_status(ctx.s.session, s_copy) == 200
        assert record_access_status(ctx.p.session, s_copy) in NO_ACCESS_STATUSES
        assert record_access_status(ctx.p.session, post) == 200
        assert record_access_status(ctx.s.session, post) in NO_ACCESS_STATUSES


# =============================================================================
# Stream (no sync) — streamed as the owner, since streaming enforces access
# =============================================================================


class TestStream:
    @pytest.mark.order(20)
    async def test_tc_ol_stream_001_mail_html(self, ctx: Ctx) -> None:
        """Mail body streams as HTML with the searchable recipient metadata prepended."""
        rec = await ctx.must_record(ctx.world.messages["basic"], "basic mail")
        resp = _stream_as(ctx, ctx.p.session, rec.id)
        assert resp.status_code == 200, resp.text[:300]
        assert "text/html" in resp.headers.get("content-type", "").lower()
        assert C.MAIL_BODY_MARKER in resp.text
        assert ctx.s.email in resp.text, "recipient metadata must be part of the streamed (indexed) HTML"

    @pytest.mark.order(21)
    async def test_tc_ol_stream_002_empty_mail(self, ctx: Ctx) -> None:
        rec = await ctx.must_record(ctx.world.messages["empty"], "empty mail")
        assert _stream_as(ctx, ctx.p.session, rec.id).status_code == 200

    @pytest.mark.order(22)
    async def test_tc_ol_stream_003_attachment_bytes(self, ctx: Ctx) -> None:
        """Attachment streams byte-exact with its filename."""
        rec = await ctx.must_record(ctx.world.attachments["txt"], "text attachment")
        resp = _stream_as(ctx, ctx.p.session, rec.id)
        assert resp.status_code == 200, resp.text[:300]
        assert resp.content == C.DEFAULT_ATTACHMENT_BYTES
        assert C.DEFAULT_ATTACHMENT_NAME in resp.headers.get("content-disposition", "")

    @pytest.mark.order(22)
    async def test_tc_ol_att_005_over_cap_attachment(self, ctx: Ctx) -> None:
        """An attachment over the indexing size cap syncs as AUTO_INDEX_OFF and still streams byte-exact."""
        if "large" not in ctx.world.attachments:
            pytest.skip("set OUTLOOK_TEST_LARGE_ATTACHMENT=1 to seed the over-cap attachment")
        assert C.LARGE_ATTACHMENT_SIZE > OutlookSyncConfig.MAX_INDEXED_ATTACHMENT_BYTES
        rec = await ctx.must_record(ctx.world.attachments["large"], "over-cap attachment")
        assert rec.indexing_status == _AUTO_INDEX_OFF, f"over-cap attachment: {rec.indexing_status}"
        resp = _stream_as(ctx, ctx.p.session, rec.id)
        assert resp.status_code == 200, resp.text[:300]
        assert len(resp.content) == C.LARGE_ATTACHMENT_SIZE, f"streamed {len(resp.content)} bytes"
        assert hashlib.sha256(resp.content).digest() == hashlib.sha256(C.large_attachment_bytes()).digest()

    @pytest.mark.order(23)
    async def test_tc_ol_stream_004_inline_image(self, ctx: Ctx) -> None:
        rec = await ctx.must_record(ctx.world.attachments["inline"], "inline image")
        resp = _stream_as(ctx, ctx.p.session, rec.id)
        assert resp.status_code == 200 and resp.content == C.PNG_1PX

    @pytest.mark.order(24)
    async def test_tc_ol_stream_005_item_attachment_is_not_a_dead_record(self, ctx: Ctx) -> None:
        """An attached Outlook item has no file bytes, so it gets no record that could never be streamed."""
        assert await ctx.record(ctx.world.attachments["item"]) is None, "item attachment synced as a dead record"
        await ctx.must_record(ctx.world.attachments["txt"], "file attachment on the same mail")

    @pytest.mark.order(25)
    async def test_tc_ol_stream_006_group_post_and_attachment(self, ctx: Ctx) -> None:
        """Group post streams HTML; its attachment streams byte-exact (no mailbox owner needed)."""
        w = ctx.world
        post = await ctx.must_record(w.group_posts["group"], "group post")
        resp = _stream_as(ctx, ctx.p.session, post.id)
        assert resp.status_code == 200 and C.GROUP_BODY_MARKER in resp.text
        att = await ctx.must_record(w.group_attachments["group"], "group post attachment")
        resp = _stream_as(ctx, ctx.p.session, att.id)
        assert resp.status_code == 200 and resp.content == C.DEFAULT_ATTACHMENT_BYTES

    @pytest.mark.order(26)
    async def test_tc_ol_stream_007_non_owner_is_refused(self, ctx: Ctx) -> None:
        """Streaming someone else's mail is refused, not served."""
        rec = await ctx.must_record(ctx.world.messages["basic"], "basic mail")
        assert _stream_as(ctx, ctx.s.session, rec.id).status_code in NO_ACCESS_STATUSES


# =============================================================================
# Connection check (no sync) — a second instance with a wrong client secret
# =============================================================================


class TestConnectionCheck:
    @pytest.mark.order(27)
    async def test_tc_ol_conn_001_wrong_client_secret(self, ctx: Ctx) -> None:
        """Enabling an instance with a wrong client secret fails with a message naming the secret."""
        real_secret = os.getenv("OUTLOOK_TEST_CLIENT_SECRET") or ""
        bad_id = None
        try:
            instance = ctx.client.create_connector(
                connector_type=OUTLOOK_CONNECTOR_TYPE,
                instance_name=ctx.world.name("bad-secret"),
                scope=C.OUTLOOK_SCOPE,
                auth_type=C.OUTLOOK_AUTH_TYPE,
                config={
                    "auth": {
                        "authType": C.OUTLOOK_AUTH_TYPE,
                        "clientId": os.getenv("OUTLOOK_TEST_CLIENT_ID"),
                        "clientSecret": f"phit-wrong-{uuid.uuid4().hex}",
                        "tenantId": os.getenv("OUTLOOK_TEST_TENANT_ID"),
                        "hasAdminConsent": True,
                    },
                    "filters": sync_filters(**baseline_sync_values(ctx.world)),
                },
            )
            bad_id = instance.connector_id
            # The UI's enable switch: the backend runs init() and the connection test before any sync.
            resp = ctx.client.request("POST", f"/api/v1/connectors/{bad_id}/toggle", json={"type": "sync"})
            text = resp.text.replace(real_secret, "<secret>") if real_secret else resp.text
            assert 400 <= resp.status_code < 500, f"enable with a wrong secret: HTTP {resp.status_code} {text[:300]}"
            assert "client secret" in text.lower(), f"the failure does not name the client secret: {text[:300]}"
            assert not ctx.client.get_connector(bad_id).get("isActive"), "instance with a wrong secret became active"
        finally:
            if bad_id:
                try:
                    ctx.client.delete_connector(bad_id)
                except Exception as e:
                    logger.warning("cleanup of the wrong-secret instance %s failed: %s", bad_id, e)


# =============================================================================
# Reindex (no sync)
# =============================================================================


class TestReindex:
    @pytest.mark.order(30)
    async def test_tc_ol_reindex_001_unchanged_mail(self, ctx: Ctx) -> None:
        """Reindexing an unchanged mail keeps its record (same id, revision, version)."""
        before = await ctx.must_record(ctx.world.messages["reindex"], "reindex mail")
        await _reindex_and_wait(ctx, ctx.p.session, [before.id])
        after = await ctx.must_record(ctx.world.messages["reindex"], "reindex mail")
        assert (after.id, after.external_revision_id, after.version) == (
            before.id, before.external_revision_id, before.version,
        )

    @pytest.mark.order(31)
    async def test_tc_ol_reindex_002_mail_changed_at_source(self, ctx: Ctx) -> None:
        """Reindex of a mail edited at source pulls the new subject and revision into the record."""
        p, m = ctx.p.source_id, ctx.world.messages
        before = await ctx.must_record(m["reindex_update"], "reindex-update mail")
        new_subject = ctx.world.name("reindex updated")
        fresh = await ctx.helper.update_message(p, m["reindex_update"], {"subject": new_subject})
        assert mail_content_revision(fresh) != before.external_revision_id
        _reindex_as(ctx, ctx.p.session, before.id)

        async def _updated() -> bool:
            rec = await ctx.record(m["reindex_update"])
            return rec is not None and rec.record_name == new_subject

        await _await_condition(_updated, "reindexed mail carries the new subject")
        after = await ctx.record(m["reindex_update"])
        assert after.id == before.id
        assert after.external_revision_id == mail_content_revision(fresh)
        ctx.world.subjects["reindex_update"] = new_subject

    @pytest.mark.order(32)
    async def test_tc_ol_reindex_003_attachment_and_group_post(self, ctx: Ctx) -> None:
        """Reindex an attachment and a group post (group path needs no mailbox owner): both stay."""
        targets = [(await ctx.must_record(ext, "reindex target")).id
                   for ext in (ctx.world.attachments["reindex_att"], ctx.world.group_posts["group"])]
        await _reindex_and_wait(ctx, ctx.p.session, targets)
        for ext in (ctx.world.attachments["reindex_att"], ctx.world.group_posts["group"]):
            await ctx.must_record(ext, "record after reindex")


# =============================================================================
# B1 — one batch of source changes, one incremental sync
# =============================================================================


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def phase_b1(ctx: Ctx) -> dict[str, Any]:
    """Apply every B1 change at the source, wait until Graph shows them, then run ONE incremental sync."""
    w, h, p = ctx.world, ctx.helper, ctx.p.source_id
    m, f, a = w.messages, w.folders, w.attachments
    state: dict[str, Any] = {}

    # Snapshot what must not change. p_reply is received mail that gets flagged below: Exchange
    # moves its changeKey, which must not count as a content change (the revision is a content hash).
    for label in ("basic", "deep", "p_reply"):
        rec = await ctx.must_record(m[label], label)
        state[f"before_{label}"] = (rec.id, rec.version, rec.external_revision_id)
    state["before_update"] = await ctx.must_record(m["update"], "update mail")
    state["before_rename"] = await ctx.must_group(f["rename"], "RenameMe")
    state["before_hard_delete"] = (await ctx.must_record(m["hard_delete"], "hard-delete mail")).id
    rec = await ctx.must_record(m["move"], "mail to move")
    state["before_move"] = (rec.id, rec.version, rec.external_revision_id)

    # Mail edits.
    await h.update_message(p, m["p_reply"], {"flag": {"flagStatus": "flagged"}, "categories": ["PHIT"]})
    state["update_subject"] = w.name("updated subject")
    state["update_fresh"] = await h.update_message(p, m["update"], {
        "subject": state["update_subject"],
        "body": {"contentType": "HTML", "content": "<p>updated body</p>"},
    })
    await h.permanent_delete_message(p, m["hard_delete"])
    # What Outlook does on "Delete": a move to Deleted Items (same immutable id).
    state["soft_deleted_id"] = await h.move_message(p, m["soft_delete"], "deleteditems")
    w.extra_message_ids.append((p, state["soft_deleted_id"]))
    state["moved_id"] = await h.move_message(p, m["move"], f["target"])
    await h.delete_attachment(p, m["att_remove"], a["att_remove_att"])
    a["added"] = (await h.add_attachment(p, m["att_add"], "phit_added.txt", b"added\n", "text/plain")).id

    # Folder edits.
    state["rename_to"] = w.name("Renamed")
    await h.rename_folder(p, f["rename"], state["rename_to"])
    await h.permanent_delete_folder(p, f["delete_folder"])
    f["move_folder"] = await h.move_folder(p, f["move_folder"], f["target"])
    new_folder = await h.create_child_folder(p, f["root"], "NewFolder")
    f["new_folder"] = new_folder.id
    f["new_sub"] = (await h.create_child_folder(p, f["new_folder"], "NewSub")).id
    w.subjects["new_mail"] = w.name("new mail")
    m["new_mail"] = (await h.create_message_in_folder(p, f["new_sub"], w.subjects["new_mail"], C.DEFAULT_MAIL_BODY_HTML)).id
    a["new_mail_att"] = (await h.add_attachment(p, m["new_mail"], "phit_new.txt", b"new\n", "text/plain")).id
    w.subjects["props_new"] = w.name("props new")
    m["props_new"] = (await h.create_message_in_folder(p, f["props"], w.subjects["props_new"], C.DEFAULT_MAIL_BODY_HTML)).id

    # Directory and group changes.
    await set_membership(h, w, ctx.s.source_id, True)
    await h.reply_all(p, m["group_sent"], f"<p>{w.name('group reply')}</p>")
    await seed_group_post(h, w, "group2", None)
    nickname = f"phit{w.run}"
    new_user = await h.create_user(
        display_name=f"{C.TEST_RESOURCE_PREFIX} User {w.run}",
        user_principal_name=f"{nickname}@{w.group_domain}", mail_nickname=nickname,
        password=f"Pht!{uuid.uuid4().hex[:12]}Aa9",
    )
    w.aad_user_ids.append(new_user.id)
    state["new_user_id"] = new_user.id
    # A second mailbox-less user in the seed group: B2 disables it, F1 enables it again.
    dis_nick = f"phitdis{w.run}"
    state["disabled_user_upn"] = f"{dis_nick}@{w.group_domain}"
    dis_user = await h.create_user(
        display_name=w.name("disabled user"), user_principal_name=state["disabled_user_upn"],
        mail_nickname=dis_nick, password=f"Pht!{uuid.uuid4().hex[:12]}Aa9",
    )
    w.aad_user_ids.append(dis_user.id)
    state["disabled_user_id"] = dis_user.id

    async def _joined() -> bool:
        # A just-created user can 404 on the member add until the directory replicates it.
        await set_membership(h, w, dis_user.id, True)
        return True

    await wait_until_outlook_condition(_joined, description="mailbox-less user joins the seed group")
    new_group = await h.create_group(
        display_name=f"{C.TEST_RESOURCE_PREFIX} Group {w.run}", mail_nickname=w.throwaway_group_mail.split("@")[0],
    )
    w.m365_group_ids.append(new_group.id)
    state["new_group_id"] = new_group.id

    # Graph is eventually consistent; only sync once every change is visible.
    async def _source_settled() -> bool:
        moved = await h.find_messages_by_subject(p, w.subjects["move"], f["target"])
        soft = await h.message_exists(p, state["soft_deleted_id"])
        # The connector reads the /groups list, which shows a new group later than GET /groups/{id}.
        group_ok = any(
            g.id == new_group.id and g.mail for g in await h.list_unified_groups()
        )
        reply_post = await _group_reply_post(h, w)
        pending = [name for name, ok in (
            ("moved mail in Target", moved), ("soft-deleted mail in Deleted Items", soft),
            ("new group's mail address", group_ok), ("reply post in the seed group", reply_post),
        ) if not ok]
        if pending:
            logger.info("B1 source not settled yet: %s", ", ".join(pending))
            return False
        state["group_reply_post_id"] = reply_post
        return True

    await wait_until_outlook_condition(_source_settled, description="B1 source changes visible", timeout=300)
    # The reply-all also leaves ordinary mail in P's mailbox (Sent Items, and Inbox as a group member).
    for folder in ("sentitems", "inbox"):
        for msg in await h.find_messages_by_subject(p, f"RE: {w.subjects['group']}", folder):
            w.extra_message_ids.append((p, msg.id))
    state["sync_count"] = await ctx.incremental_sync("B1 incremental sync")
    return state


async def _group_reply_post(h: OutlookSourceHelper, w: OutlookWorld) -> str | None:
    """The post P's reply-all added: in the original thread, or a new thread of the same conversation."""
    for post in await h.list_thread_posts(w.group_id, w.group_threads["group"]):
        if post.id != w.group_posts["group"]:
            return post.id
    for thread in await h.list_group_threads(w.group_id):
        if thread.id != w.group_threads["group"] and (thread.topic or "").endswith(w.subjects["group"]):
            w.group_threads["group_reply"] = thread.id
            posts = await h.list_thread_posts(w.group_id, thread.id)
            if posts:
                return posts[0].id
    return None


class TestB1Incremental:
    @pytest.mark.order(40)
    async def test_tc_ol_incr_001_mail_edited(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """Edited mail: same record, new subject and revision, version bumped."""
        before = phase_b1["before_update"]
        rec = await ctx.must_record(ctx.world.messages["update"], "edited mail")
        assert rec.id == before.id
        assert rec.record_name == phase_b1["update_subject"]
        assert rec.external_revision_id == mail_content_revision(phase_b1["update_fresh"])
        assert rec.external_revision_id != before.external_revision_id, "the revision must move so the edit re-indexes"
        assert rec.version > before.version

    @pytest.mark.order(41)
    async def test_tc_ol_incr_002_mail_hard_deleted(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """Permanently deleted mail: the record and its attachment are gone."""
        assert await ctx.record(ctx.world.messages["hard_delete"]) is None
        assert await ctx.record(ctx.world.attachments["hard_delete_att"]) is None, "orphan attachment left behind"

    @pytest.mark.order(42)
    async def test_tc_ol_incr_003_mail_soft_deleted(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """Deleted (→ Deleted Items) mail is gone from search: Deleted Items is not synced by default."""
        assert phase_b1["soft_deleted_id"] == ctx.world.messages["soft_delete"], "immutable id"
        assert await ctx.record(ctx.world.messages["soft_delete"]) is None
        assert await ctx.group(await ctx.folder_id(ctx.p.source_id, "deleteditems")) is None

    @pytest.mark.order(43)
    async def test_tc_ol_incr_004_mail_moved(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """Moved mail keeps its immutable id and its record, under the target folder, not re-indexed."""
        assert phase_b1["moved_id"] == ctx.world.messages["move"], "immutable id"
        rec = await ctx.must_record(phase_b1["moved_id"], "moved mail")
        assert rec.external_record_group_id == ctx.world.folders["target"]
        assert (rec.id, rec.version, rec.external_revision_id) == phase_b1["before_move"]
        await ctx.assert_access(ctx.p, {"moved mail": rec.id}, {})

    @pytest.mark.order(44)
    async def test_tc_ol_incr_005_attachment_removed_and_added(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """An attachment removed at source disappears; one added to a synced mail appears under it."""
        a, m = ctx.world.attachments, ctx.world.messages
        assert await ctx.record(a["att_remove_att"]) is None, "removed attachment still synced"
        added = await ctx.must_record(a["added"], "added attachment")
        assert added.parent_external_record_id == m["att_add"]

    @pytest.mark.order(45)
    async def test_tc_ol_incr_006_folder_renamed(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        rg = await ctx.must_group(ctx.world.folders["rename"], "renamed folder")
        assert rg.id == phase_b1["before_rename"].id
        assert rg.name == phase_b1["rename_to"]

    @pytest.mark.order(46)
    async def test_tc_ol_incr_007_folder_deleted(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """A deleted folder's RecordGroup and its mail are removed."""
        assert await ctx.group(ctx.world.folders["delete_folder"]) is None, "deleted folder still synced"
        assert await ctx.record(ctx.world.messages["in_deleted_folder"]) is None, "mail of a deleted folder still synced"

    @pytest.mark.order(47)
    async def test_tc_ol_incr_008_folder_moved(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """A folder moved under another hangs under the new parent; its mail stays readable by the owner."""
        f = ctx.world.folders
        rg = await ctx.must_group(f["move_folder"], "moved folder")
        assert rg.parent_external_group_id == f["target"]
        target = await ctx.must_group(f["target"], "Target")
        assert await ctx.edges(_RECORD_GROUPS, rg.id, _RECORD_GROUPS, target.id, _INHERIT)
        old_parent = await ctx.must_group(f["root"], "root")
        assert not await ctx.edges(_RECORD_GROUPS, rg.id, _RECORD_GROUPS, old_parent.id, _BELONGS_TO), (
            "moved folder still hangs under its old parent"
        )
        mail = await ctx.must_record(ctx.world.messages["in_moved_folder"], "mail in the moved folder")
        await ctx.assert_access(ctx.p, {"mail in moved folder": mail.id}, {})

    @pytest.mark.order(48)
    async def test_tc_ol_incr_009_folder_and_mail_added(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """New folder + subfolder + mail with attachment, and a new mail in an existing folder."""
        w = ctx.world
        assert (await ctx.must_group(w.folders["new_folder"], "new folder")).parent_external_group_id == w.folders["root"]
        assert (await ctx.must_group(w.folders["new_sub"], "new subfolder")).parent_external_group_id == w.folders["new_folder"]
        mail = await ctx.must_record(w.messages["new_mail"], "new mail")
        att = await ctx.must_record(w.attachments["new_mail_att"], "new mail's attachment")
        props_new = await ctx.must_record(w.messages["props_new"], "new mail in existing folder")
        await ctx.assert_access(ctx.p, {"new mail": mail.id, "its attachment": att.id, "props new": props_new.id}, {})
        await ctx.assert_access(ctx.s, {}, {"new mail": mail.id, "its attachment": att.id})

    @pytest.mark.order(49)
    async def test_tc_ol_incr_010_member_added_gains_access(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """S joined the seed group: membership edge appears and S can read the group's posts."""
        w = ctx.world
        group_node = await ctx.graph.get_user_group_by_external_id(ctx.cid, w.group_id)
        s_node = await ctx.graph.get_user_by_source_id(ctx.s.source_id, ctx.cid)
        assert await ctx.edges(_USERS, s_node.id, _GROUPS, group_node.id, _PERMISSION), "S membership edge missing"
        post = await ctx.must_record(w.group_posts["group"], "group post")
        await ctx.assert_access(ctx.s, {"group post": post.id}, {})
        wait_for_record_access(ctx.s.session, post.id, expect_access=True, description="group post after joining")

    @pytest.mark.order(50)
    async def test_tc_ol_incr_011_group_posts_added(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """A reply in an existing conversation and a brand-new conversation are both synced."""
        reply = await ctx.must_record(phase_b1["group_reply_post_id"], "reply post in existing thread")
        assert reply.record_type == RecordType.GROUP_MAIL
        await ctx.must_record(ctx.world.group_posts["group2"], "post of a new conversation")
        first = await ctx.must_record(ctx.world.group_posts["group"], "original post")
        assert first.version == 0, "an untouched post must not be re-versioned by an incremental sync"

    @pytest.mark.order(51)
    async def test_tc_ol_incr_012_aad_user_added(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """A user added to the directory is synced as an app user."""
        user = await ctx.graph.get_user_by_source_id(phase_b1["new_user_id"], ctx.cid)
        assert user is not None, "new AAD user not synced"

    @pytest.mark.order(51)
    async def test_tc_ol_user_002_member_without_mailbox_reads_group_posts(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """A new user with no mailbox in the seed group is linked to the connector and reads its posts."""
        assert await ctx.graph.principal_app_relation(ctx.cid, phase_b1["disabled_user_upn"]) is not None
        node = await ctx.graph.get_user_by_source_id(phase_b1["disabled_user_id"], ctx.cid)
        assert node is not None, "mailbox-less user not synced"
        post = await ctx.must_record(ctx.world.group_posts["group"], "group post")
        assert post.id in await ctx.admitted(node.id, post.id), "group member cannot read the group's post"

    @pytest.mark.order(52)
    async def test_tc_ol_incr_013_m365_group_added(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """A new M365 group in scope becomes an AppUserGroup and a group mailbox."""
        gid = phase_b1["new_group_id"]
        assert await ctx.graph.get_user_group_by_external_id(ctx.cid, gid) is not None, "new group not synced"
        await ctx.must_group(gid, "new group's mailbox")

    @pytest.mark.order(53)
    async def test_tc_ol_incr_014_untouched_records_unchanged(self, ctx: Ctx, phase_b1: dict[str, Any]) -> None:
        """Untouched records, and a received mail that was only flagged, keep id, version and revision."""
        for label in ("basic", "deep", "p_reply"):
            rec = await ctx.must_record(ctx.world.messages[label], label)
            assert (rec.id, rec.version, rec.external_revision_id) == phase_b1[f"before_{label}"], (
                f"{label} was rewritten by an incremental sync that did not change its content"
            )
        parents = await ctx.graph.get_record_incoming_relations(ctx.cid, ctx.world.messages["p_reply"], "SIBLING")
        assert parents == [ctx.world.messages["p_sent"]], f"thread link duplicated or lost: {parents}"


# =============================================================================
# B2 — removals + a run with nothing else to do
# =============================================================================


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def phase_b2(ctx: Ctx, phase_b1: dict[str, Any]) -> dict[str, Any]:
    w, h = ctx.world, ctx.helper
    state: dict[str, Any] = {"before": await _record_ids(ctx)}
    for label in ("basic", "props_new"):
        rec = await ctx.must_record(w.messages[label], label)
        state[f"before_{label}"] = (rec.id, rec.version, rec.external_revision_id)
    await set_membership(h, w, ctx.s.source_id, False)
    uid = state["disabled_user_id"] = phase_b1["disabled_user_id"]
    state["disabled_user_upn"] = phase_b1["disabled_user_upn"]
    node = await ctx.graph.get_user_by_source_id(uid, ctx.cid)
    state["disabled_node_id"] = node.id if node is not None else None
    await h.set_account_enabled(uid, False)

    async def _disabled() -> bool:
        return await h.account_enabled(uid) is False

    await wait_until_outlook_condition(_disabled, description="throwaway user disabled")
    await h.delete_group(phase_b1["new_group_id"])
    w.m365_group_ids.remove(phase_b1["new_group_id"])
    w.deleted_directory_ids.append(phase_b1["new_group_id"])
    state["user_delete_refused"] = await retire_user(h, w, phase_b1["new_user_id"])

    async def _gone() -> bool:
        # The connector reads the /groups list, which keeps a deleted group for a while after
        # GET /groups/{id} already answers 404.
        listed = {g.id for g in await h.list_unified_groups()}
        return phase_b1["new_group_id"] not in listed

    await wait_until_outlook_condition(_gone, description="throwaway group gone from the group list", timeout=600)
    state["sync_count"] = await ctx.incremental_sync("B2 incremental sync")
    return state


async def _record_ids(ctx: Ctx) -> set[str]:
    return {r["externalRecordId"] for r in await ctx.graph.fetch_records_by_type(ctx.cid, "") if r.get("externalRecordId")}


def _world_external_ids(w: OutlookWorld) -> set[str]:
    return (
        set(w.messages.values()) | set(w.attachments.values()) | set(w.group_posts.values())
        | set(w.group_attachments.values()) | set(w.bulk_message_ids)
    )


class TestB2Removals:
    @pytest.mark.order(60)
    async def test_tc_ol_user_003_disabled_account_loses_access(self, ctx: Ctx, phase_b2: dict[str, Any]) -> None:
        """A disabled account loses the connector: no user-app relation, the group's posts unreachable."""
        assert phase_b2["disabled_node_id"], "mailbox-less user was not synced in B1"
        assert await ctx.graph.principal_app_relation(ctx.cid, phase_b2["disabled_user_upn"]) is None, (
            "disabled account is still linked to the connector"
        )
        post = await ctx.must_record(ctx.world.group_posts["group"], "group post")
        assert not await ctx.admitted(phase_b2["disabled_node_id"], post.id), "disabled account still reads group posts"

    @pytest.mark.order(60)
    async def test_tc_ol_incr_020_member_removed_loses_access(self, ctx: Ctx, phase_b2: dict[str, Any]) -> None:
        """S left the seed group: membership edge gone and S can no longer read the posts."""
        w = ctx.world
        group_node = await ctx.graph.get_user_group_by_external_id(ctx.cid, w.group_id)
        s_node = await ctx.graph.get_user_by_source_id(ctx.s.source_id, ctx.cid)
        assert not await ctx.edges(_USERS, s_node.id, _GROUPS, group_node.id, _PERMISSION), "stale membership edge"
        post = await ctx.must_record(w.group_posts["group"], "group post")
        await ctx.assert_access(ctx.s, {}, {"group post": post.id})
        wait_for_record_access(ctx.s.session, post.id, expect_access=False, description="group post after leaving")

    @pytest.mark.order(61)
    async def test_tc_ol_incr_021_m365_group_deleted(self, ctx: Ctx, phase_b1: dict[str, Any], phase_b2: dict[str, Any]) -> None:
        """A deleted M365 group no longer grants anything."""
        gid = phase_b1["new_group_id"]
        assert await ctx.graph.get_user_group_by_external_id(ctx.cid, gid) is None, "deleted group still synced"
        assert await ctx.graph.count_permission_edges_to_record_groups(ctx.cid, gid) == 0, (
            "deleted group's mailbox still carries a grant"
        )

    @pytest.mark.order(62)
    @pytest.mark.xfail(
        strict=True,
        reason="platform gap: no connector can remove a user it synced — "
        "DataSourceEntitiesProcessor.on_user_removed is commented out (TODO: delete the userAppRelation)",
    )
    async def test_tc_ol_incr_022_aad_user_deleted(self, ctx: Ctx, phase_b1: dict[str, Any], phase_b2: dict[str, Any]) -> None:
        """A user deleted from the directory is no longer linked to the connector."""
        if phase_b2["user_delete_refused"]:
            pytest.skip(
                "the test app may not delete directory users (disabled and renamed it instead): "
                f"{phase_b2['user_delete_refused']}"
            )
        user = await ctx.graph.get_user_by_source_id(phase_b1["new_user_id"], ctx.cid)
        assert user is None, "deleted AAD user is still synced as an app user"

    @pytest.mark.order(63)
    async def test_tc_ol_incr_023_idempotent_rerun(self, ctx: Ctx, phase_b2: dict[str, Any]) -> None:
        """A run with no mail changes neither adds, drops nor rewrites any of this run's records.

        The two mailboxes are real, so mail outside the run may come and go; only the run's
        own records are held to this, and any other change is logged.
        """
        before, after = phase_b2["before"], await _record_ids(ctx)
        ours = _world_external_ids(ctx.world)
        lost = sorted((before - after) & ours)
        gained = sorted((after - before) & ours)
        assert not lost and not gained, f"no-op run changed the run's records: lost={lost} gained={gained}"
        other = sorted((before ^ after) - ours)
        if other:
            logger.info("records outside the run changed during B2 (real mailbox traffic): %s", other)
        for label in ("basic", "props_new"):
            rec = await ctx.must_record(ctx.world.messages[label], label)
            assert (rec.id, rec.version, rec.external_revision_id) == phase_b2[f"before_{label}"]


# =============================================================================
# F1–F3 — filter changes (each forces a full sync), then restore
# =============================================================================


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def phase_f1(ctx: Ctx, phase_b2: dict[str, Any]) -> None:
    # Indexing toggles apply to records first synced while they are off; content already
    # indexed stays COMPLETED (the processor never downgrades it). So F1 brings new items.
    w, h = ctx.world, ctx.helper
    uid = phase_b2["disabled_user_id"]
    await h.set_account_enabled(uid, True)

    async def _enabled() -> bool:
        return await h.account_enabled(uid) is True

    await wait_until_outlook_condition(_enabled, description="throwaway user enabled again")
    w.subjects["f1_mail"] = w.name("f1 mail")
    await h.send_mail(
        ctx.s.source_id, w.subjects["f1_mail"], f"<p>{C.MAIL_BODY_MARKER} f1</p>", [ctx.p.email],
        attachments=[("phit_f1.txt", b"f1 attachment", "text/plain")],
    )
    p_copy = await poll_message(h, ctx.p.source_id, w.subjects["f1_mail"], "inbox", "F1 mail in P inbox")
    w.messages["f1_mail"] = p_copy.id
    w.extra_message_ids.append((ctx.p.source_id, p_copy.id))
    s_sent = await poll_message(h, ctx.s.source_id, w.subjects["f1_mail"], "sentitems", "F1 mail in S sent items")
    w.extra_message_ids.append((ctx.s.source_id, s_sent.id))
    await seed_group_post(h, w, "group_f1", ("phit_group_f1.txt", b"group f1 attachment", "text/plain"))
    values = dict(baseline_sync_values(ctx.world))
    values["folders"] = list_value("in", ["Inbox", "Sent Items"])
    values["custom_folders"] = bool_value(False)
    indexing = {key: bool_value(False) for key in ("mails", "attachments", "group_conversations")}
    await ctx.filter_sync("F1 standard folders only + indexing off", values, indexing)


class TestF1StandardFoldersIndexingOff:
    @pytest.mark.order(70)
    async def test_tc_ol_filter_001_custom_folders_excluded(self, ctx: Ctx, phase_f1: None) -> None:
        """custom_folders=false + folders=[Inbox, Sent Items]: custom folders and Drafts become unreachable."""
        f, m = ctx.world.folders, ctx.world.messages
        root = await ctx.must_group(f["root"], "root")
        drafts = await ctx.group(await ctx.folder_id(ctx.p.source_id, "drafts"))
        basic = await ctx.must_record(m["basic"], "basic (synced earlier)")
        sent = await ctx.must_record(m["p_sent"], "P sent copy")
        reply = await ctx.must_record(m["p_reply"], "P reply (Inbox)")
        denied = {"root folder": root.id, "mail in custom folder": basic.id}
        if drafts is not None:
            denied["Drafts"] = drafts.id
        await ctx.assert_access(ctx.p, {"Sent Items mail": sent.id, "Inbox mail": reply.id}, denied)

    @pytest.mark.order(71)
    async def test_tc_ol_filter_002_indexing_off(self, ctx: Ctx, phase_f1: None) -> None:
        """mails / attachments / group_conversations off: new items sync as AUTO_INDEX_OFF, still readable.

        Already-indexed content keeps COMPLETED: switching indexing off does not un-index it.
        """
        w, m = ctx.world, ctx.world.messages
        mail = await ctx.must_record(m["f1_mail"], "mail received while indexing is off")
        assert mail.indexing_status == _AUTO_INDEX_OFF, f"mail: {mail.indexing_status}"
        att_src = (await ctx.helper.list_attachments(ctx.p.source_id, m["f1_mail"]))[0]
        att = await ctx.must_record(att_src.id, "its attachment")
        assert att.indexing_status == _AUTO_INDEX_OFF, f"attachment: {att.indexing_status}"
        post = await ctx.must_record(w.group_posts["group_f1"], "group post while indexing is off")
        assert post.indexing_status == _AUTO_INDEX_OFF, f"group post: {post.indexing_status}"
        post_att = await ctx.must_record(w.group_attachments["group_f1"], "its attachment")
        assert post_att.indexing_status == _AUTO_INDEX_OFF, f"group attachment: {post_att.indexing_status}"
        sent = await ctx.must_record(m["p_sent"], "mail indexed before the toggle")
        assert sent.indexing_status != _AUTO_INDEX_OFF, "indexing-off must not un-index existing content"
        await ctx.assert_access(ctx.p, {"new mail": mail.id, "new group post": post.id}, {})


    @pytest.mark.order(72)
    async def test_tc_ol_user_004_reenabled_account_regains_access(
        self, ctx: Ctx, phase_b2: dict[str, Any], phase_f1: None,
    ) -> None:
        """Enabling the account again links it to the connector and restores its group access."""
        assert await ctx.graph.principal_app_relation(ctx.cid, phase_b2["disabled_user_upn"]) is not None, (
            "re-enabled account is not linked to the connector"
        )
        node = await ctx.graph.get_user_by_source_id(phase_b2["disabled_user_id"], ctx.cid)
        assert node is not None
        post = await ctx.must_record(ctx.world.group_posts["group"], "group post")
        assert post.id in await ctx.admitted(node.id, post.id), "re-enabled account cannot read group posts"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def phase_f1b(ctx: Ctx, phase_f1: None) -> None:
    values = dict(baseline_sync_values(ctx.world))
    values["custom_folders"] = bool_value(False)
    await ctx.filter_sync("F1b custom folders off, no standard selection", values, {})


class TestF1bStandardFoldersOnly:
    @pytest.mark.order(75)
    async def test_tc_ol_filter_003_custom_off_all_standard(self, ctx: Ctx, phase_f1b: None) -> None:
        """custom_folders=false with nothing selected: every standard folder but Deleted Items syncs, custom folders do not."""
        m, f = ctx.world.messages, ctx.world.folders
        root = await ctx.must_group(f["root"], "root")
        basic = await ctx.must_record(m["basic"], "custom folder mail")
        sent = await ctx.must_record(m["p_sent"], "Sent Items mail")
        reply = await ctx.must_record(m["p_reply"], "Inbox mail")
        assert await ctx.group(await ctx.folder_id(ctx.p.source_id, "deleteditems")) is None
        await ctx.assert_access(
            ctx.p,
            {"Sent Items mail": sent.id, "Inbox mail": reply.id},
            {"root folder": root.id, "custom folder mail": basic.id},
        )


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def phase_f2(ctx: Ctx, phase_f1b: None) -> None:
    w = ctx.world
    values = dict(baseline_sync_values(w))
    values["users"] = list_value("in", [ctx.s.email.upper()])  # matched case-insensitively
    values["groups"] = list_value("not_in", [w.group_mail, w.throwaway_group_mail])
    await ctx.filter_sync("F2 users=[S], groups excluded", values, {})


class TestF2UsersAndGroups:
    @pytest.mark.order(80)
    async def test_tc_ol_filter_010_user_excluded(self, ctx: Ctx, phase_f2: None) -> None:
        """users=[S]: P's mailbox loses its grant and P's mail is unreachable; S's mail is unaffected."""
        m = ctx.world.messages
        p_mailbox = await ctx.mailbox(ctx.p.source_id)
        p_node = await ctx.graph.get_user_by_source_id(ctx.p.source_id, ctx.cid)
        assert not await ctx.edges(_USERS, p_node.id, _RECORD_GROUPS, p_mailbox.id, _PERMISSION), (
            "excluded user's mailbox still carries the owner grant"
        )
        basic = await ctx.must_record(m["basic"], "basic")
        sent = await ctx.must_record(m["p_sent"], "P sent")
        s_copy = await ctx.must_record(m["s_copy"], "S copy")
        await ctx.assert_access(ctx.p, {}, {"P mail": basic.id, "P sent": sent.id})
        await ctx.assert_access(ctx.s, {"S copy": s_copy.id}, {})

    @pytest.mark.order(81)
    async def test_tc_ol_filter_011_group_excluded(self, ctx: Ctx, phase_f2: None) -> None:
        """groups not_in [seed]: the group mailbox has no grant and members lose the posts."""
        w = ctx.world
        assert await ctx.graph.count_permission_edges_to_record_groups(ctx.cid, w.group_id) == 0
        post = await ctx.must_record(w.group_posts["group"], "group post")
        await ctx.assert_access(ctx.p, {}, {"group post": post.id})


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def phase_f3(ctx: Ctx, phase_f2: None) -> None:
    values = dict(baseline_sync_values(ctx.world))
    values["folders"] = list_value("in", ["Inbox"])
    values["custom_folders"] = bool_value(True)
    # Only PipesHub users get a mailbox synced, so not_in [S] leaves P alone, not the whole directory.
    values["users"] = list_value("not_in", [ctx.s.email.upper()])
    await ctx.filter_sync("F3 folders=[Inbox] + custom, users not_in [S]", values, {})


class TestF3SelectedStandardPlusCustom:
    @pytest.mark.order(90)
    async def test_tc_ol_filter_020_selected_standard_plus_custom(self, ctx: Ctx, phase_f3: None) -> None:
        """folders=[Inbox] + custom on: Inbox and custom folders sync; other standard folders do not."""
        m = ctx.world.messages
        basic = await ctx.must_record(m["basic"], "custom folder mail")
        reply = await ctx.must_record(m["p_reply"], "Inbox mail")
        sent = await ctx.must_record(m["p_sent"], "Sent Items mail")
        await ctx.assert_access(ctx.p, {"custom folder mail": basic.id, "Inbox mail": reply.id}, {"Sent Items mail": sent.id})

    @pytest.mark.order(91)
    async def test_tc_ol_filter_021_users_not_in_excludes_only_that_mailbox(self, ctx: Ctx, phase_f3: None) -> None:
        """users not_in [S]: S's mailbox loses its grant and S's mail is unreachable; P keeps theirs."""
        s_mailbox = await ctx.mailbox(ctx.s.source_id)
        s_node = await ctx.graph.get_user_by_source_id(ctx.s.source_id, ctx.cid)
        assert not await ctx.edges(_USERS, s_node.id, _RECORD_GROUPS, s_mailbox.id, _PERMISSION), (
            "excluded user's mailbox still carries the owner grant"
        )
        p_mailbox = await ctx.mailbox(ctx.p.source_id)
        p_node = await ctx.graph.get_user_by_source_id(ctx.p.source_id, ctx.cid)
        assert await ctx.edges(_USERS, p_node.id, _RECORD_GROUPS, p_mailbox.id, _PERMISSION), (
            "a user not in the exclusion lost their mailbox"
        )
        s_copy = await ctx.must_record(ctx.world.messages["s_copy"], "S copy")
        await ctx.assert_access(ctx.s, {}, {"S copy": s_copy.id})


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def phase_f4(ctx: Ctx, phase_f3: None) -> None:
    values = dict(baseline_sync_values(ctx.world))
    values["folders"] = list_value("not_in", ["Inbox"])
    values["custom_folders"] = bool_value(True)
    await ctx.filter_sync("F4 folders not_in [Inbox] + custom", values, {})


class TestF4StandardFoldersNotIn:
    @pytest.mark.order(95)
    async def test_tc_ol_filter_025_standard_folders_not_in(self, ctx: Ctx, phase_f4: None) -> None:
        """folders not_in [Inbox] + custom on: Inbox mail drops out, other standard and custom folders stay."""
        m = ctx.world.messages
        reply = await ctx.must_record(m["p_reply"], "P Inbox mail")
        sent = await ctx.must_record(m["p_sent"], "P Sent Items mail")
        basic = await ctx.must_record(m["basic"], "custom folder mail")
        s_copy = await ctx.must_record(m["s_copy"], "S Inbox mail")
        await ctx.assert_access(
            ctx.p, {"Sent Items mail": sent.id, "custom folder mail": basic.id}, {"Inbox mail": reply.id},
        )
        await ctx.assert_access(ctx.s, {}, {"S Inbox mail": s_copy.id})
        # not_in selects the other standard folders, which never includes Deleted Items.
        assert await ctx.record(m["in_deleted_items"]) is None, "Deleted Items mail synced under not_in"


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def phase_restore(ctx: Ctx, phase_f4: None) -> dict[str, Any]:
    m = ctx.world.messages
    state = {"ids": {label: (await ctx.must_record(m[label], label)).id for label in ("basic", "p_sent", "s_copy")}}
    await ctx.filter_sync("R restore baseline", baseline_sync_values(ctx.world), {})
    return state


class TestRestore:
    @pytest.mark.order(100)
    async def test_tc_ol_filter_030_restore_baseline(self, ctx: Ctx, phase_restore: dict[str, Any]) -> None:
        """Back to baseline: same records (no duplicates), access restored, indexing no longer off."""
        w, m = ctx.world, ctx.world.messages
        for label, rid in phase_restore["ids"].items():
            rec = await ctx.must_record(m[label], label)
            assert rec.id == rid, f"{label} was recreated with a new id instead of reused"
        basic = await ctx.must_record(m["basic"], "basic")
        sent = await ctx.must_record(m["p_sent"], "P sent")
        post = await ctx.must_record(w.group_posts["group"], "group post")
        await ctx.assert_access(ctx.p, {"basic": basic.id, "P sent": sent.id, "group post": post.id}, {})
        await ctx.assert_access(ctx.s, {"S copy": phase_restore["ids"]["s_copy"]}, {})
        for label, ext in (("F1 mail", m["f1_mail"]), ("F1 group post", w.group_posts["group_f1"])):
            rec = await ctx.must_record(ext, label)
            assert rec.indexing_status != _AUTO_INDEX_OFF, f"{label} still AUTO_INDEX_OFF after indexing was re-enabled"


# =============================================================================
# FS — the UI's forced full sync over an unchanged scope
# =============================================================================


async def _sync_snapshot(ctx: Ctx) -> dict[str, Any]:
    by_ext: dict[str, list[str]] = {}
    records = await ctx.graph.fetch_records_by_type(ctx.cid, "")
    for r in records:
        if r.get("externalRecordId"):
            by_ext.setdefault(r["externalRecordId"], []).append(str(r.get("_key") or r.get("id")))
    return {"records": by_ext, "record_count": len(records), "group_count": await ctx.graph.count_record_groups(ctx.cid)}


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def phase_full_resync(ctx: Ctx, phase_restore: dict[str, Any]) -> dict[str, Any]:
    before = await _sync_snapshot(ctx)
    await run_sync_and_wait(
        ctx.client, ctx.graph, ctx.cid, lambda: ctx.client.resync_connector(ctx.cid, full_sync=True),
        description="FS forced full sync",
    )
    return {"before": before, "after": await _sync_snapshot(ctx)}


class TestFullResync:
    @pytest.mark.order(110)
    async def test_tc_ol_sync_002_forced_full_sync_is_idempotent(self, ctx: Ctx, phase_full_resync: dict[str, Any]) -> None:
        """A forced full sync keeps every record id and record group: no duplicates, nothing lost.

        Mail outside the run may still arrive in the real mailboxes; it is logged, not held to this.
        """
        before, after = phase_full_resync["before"], phase_full_resync["after"]
        dups = {ext: ids for ext, ids in after["records"].items() if len(ids) > 1}
        assert not dups, f"full sync duplicated records: {dups}"
        ours = _world_external_ids(ctx.world)
        changed = sorted(ext for ext in ours if before["records"].get(ext) != after["records"].get(ext))
        assert not changed, f"full sync lost, added or recreated the run's records: {changed}"
        gained = set(after["records"]) - set(before["records"]) - ours
        lost = set(before["records"]) - set(after["records"]) - ours
        if gained or lost:
            logger.info("records outside the run changed during FS (real mailbox traffic): +%s -%s", sorted(gained), sorted(lost))
        assert after["record_count"] - len(gained) + len(lost) == before["record_count"], (
            f"record count {before['record_count']} -> {after['record_count']} beyond mail outside the run"
        )
        assert after["group_count"] == before["group_count"], (
            f"record groups {before['group_count']} -> {after['group_count']}"
        )
