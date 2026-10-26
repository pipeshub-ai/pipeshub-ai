# pyright: ignore-file

"""Gmail Workspace connector ITs against the Gmail simulator (design: connectors/gmail/DESIGN.md).

  order 0  TC-GMAIL-WS-001 — first sync: every in-scope message and attachment, nothing from trash or spam
  order 1  TC-GMAIL-WS-002 — mailbox record groups and the owner's grant
  order 2  TC-GMAIL-WS-003 — the owner opens their mail; the second user cannot
  order 3  TC-GMAIL-WS-004 — incremental: new mail, including mail a filter archived
  order 4  TC-GMAIL-WS-005 — archive moves a message to OTHERS, unarchive back, no re-index
  order 5  TC-GMAIL-WS-006 — trash removes a message and its attachments; untrash restores it
  order 6  TC-GMAIL-WS-007 — permanent delete and spam remove a message
  order 7  TC-GMAIL-WS-008 — expired history: the mailbox is read again and deleted mail removed
  order 8  TC-GMAIL-WS-009 — narrowing the Received Date filter takes old mail out of scope; widening puts it back
  order 9  TC-GMAIL-WS-010 — a message streams its body to its owner; an attachment its exact bytes
  order 10 TC-GMAIL-WS-011 — suspending a user takes their mailbox grant away; reinstating restores it
  order 11 TC-GMAIL-WS-012 — delegation removed: the sync fails and removes nothing; restored, it succeeds
TC-GMAIL-WS-013 (connector deletion removes its data) is the fixture's teardown.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
import requests

from connectors.gmail_workspace.gmail_workspace_scenario import OWNER_NOT_SYNCED, OWNER_SYNCED
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # type: ignore[import-not-found]
    connector_filters,
    sync_and_wait,
)
from connectors.outlook.outlook_test_utils import run_sync_and_wait  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import (  # type: ignore[import-not-found]
    async_poll_until,
    wait_for_sync_completion,
)
from helper.record_access import wait_for_record_access  # type: ignore[import-not-found]
from helper.second_user import (  # type: ignore[import-not-found]
    SecondUser,
    log_in_existing_user,
    log_out_existing_user,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.gmail_workspace,
    pytest.mark.asyncio(loop_scope="session"),
]

_GRAPH_WAIT_SEC = 180
DAY_MS = 86_400_000


async def _present(graph: GraphProviderProtocol, connector_id: str, external_id: str) -> bool:
    return await graph.get_record_by_external_id(connector_id, external_id) is not None


async def _wait_present(graph: GraphProviderProtocol, connector_id: str, external_id: str, what: str,
                        present: bool = True) -> None:
    async def check() -> bool | None:
        return (await _present(graph, connector_id, external_id)) is present or None

    await async_poll_until(check, timeout=_GRAPH_WAIT_SEC, interval=3,
                           description=f"{what} {'present' if present else 'absent'}")


async def _wait_in_group(graph: GraphProviderProtocol, connector_id: str, external_id: str, group: str, what: str,
                         member: bool = True) -> None:
    async def check() -> bool | None:
        return (await graph.record_belongs_to_external_group(connector_id, external_id, group)) is member or None

    await async_poll_until(check, timeout=_GRAPH_WAIT_SEC, interval=3,
                           description=f"{what} {'in' if member else 'out of'} {group}")


async def _filter_sync(client: PipeshubClient, graph: GraphProviderProtocol, connector_id: str,
                       filters: dict[str, Any], what: str) -> None:
    await run_sync_and_wait(client, graph, connector_id,
                            lambda: client.update_connector_filters_sync_safe(connector_id, filters=filters),
                            description=what)


def _stream_as(client: PipeshubClient, user: SecondUser, record_id: str) -> requests.Response:
    """Stream with the mailbox owner's own session: in a Workspace only they can open their mail."""
    return requests.get(f"{client.base_url}{client._stream_record_path(record_id)}", headers=user.headers,
                        timeout=120)


class TestGmailWorkspace:
    @pytest.mark.order(0)
    async def test_tc_gmail_ws_001_first_sync(self, gmail_ws: dict[str, Any],
                                              graph_provider: GraphProviderProtocol) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        for key in OWNER_SYNCED:
            msg = sim.message(env.test_user, key)
            assert await _present(graph_provider, cid, msg["id"]), f"message {key} is synced"
            for att in msg["attachments"]:
                ext = f"{msg['id']}~{att['part']}"
                assert await _present(graph_provider, cid, ext), f"attachment {att['filename']} of {key} is synced"
                assert await graph_provider.get_record_parent_external_id(cid, ext) == msg["id"], \
                    f"attachment {att['filename']} hangs under its message"
        for key in OWNER_NOT_SYNCED:
            msg = sim.message(env.test_user, key)
            assert not await _present(graph_provider, cid, msg["id"]), f"message {key} (trash/spam) is not synced"
        second = sim.message(env.second_user, "for-second")
        assert await _present(graph_provider, cid, second["id"]), "the second user's mailbox is synced"
        assert not sim.unmodeled(), f"the connector made requests the simulator does not model: {sim.unmodeled()}"

    @pytest.mark.order(1)
    async def test_tc_gmail_ws_002_mailbox_groups(self, gmail_ws: dict[str, Any],
                                                  graph_provider: GraphProviderProtocol) -> None:
        env, cid = gmail_ws["env"], gmail_ws["connector_id"]
        for email in (env.test_user, env.second_user):
            assert await graph_provider.get_record_group_by_external_id(cid, f"mailbox:{email}"), \
                f"{email} has a mailbox record group"
            for label in ("INBOX", "SENT", "OTHERS"):
                assert await graph_provider.get_record_group_by_external_id(cid, f"{email}:{label}"), \
                    f"{email} has a {label} group"
        sim = gmail_ws["sim"]
        assert await graph_provider.record_belongs_to_external_group(
            cid, sim.message(env.test_user, "filtered")["id"], f"{env.test_user}:OTHERS"), \
            "mail a filter archived sits in OTHERS"
        assert await graph_provider.record_belongs_to_external_group(
            cid, sim.message(env.test_user, "kickoff-re")["id"], f"{env.test_user}:SENT"), "a sent reply sits in SENT"

    @pytest.mark.order(2)
    async def test_tc_gmail_ws_003_access_isolation(self, gmail_ws: dict[str, Any],
                                                    graph_provider: GraphProviderProtocol,
                                                    pipeshub_client: PipeshubClient) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        record = await graph_provider.get_record_by_external_id(cid, sim.message(env.test_user, "kickoff")["id"])
        assert record is not None
        owner = log_in_existing_user(pipeshub_client, gmail_ws["user_ids"][env.test_user], env.test_user)
        second = log_in_existing_user(pipeshub_client, gmail_ws["user_ids"][env.second_user], env.second_user)
        try:
            wait_for_record_access(owner, record.id, expect_access=True, description="their own message")
            wait_for_record_access(second, record.id, expect_access=False, description="another user's message")
        finally:
            log_out_existing_user(pipeshub_client, owner)
            log_out_existing_user(pipeshub_client, second)

    @pytest.mark.order(3)
    async def test_tc_gmail_ws_004_incremental(self, gmail_ws: dict[str, Any], graph_provider: GraphProviderProtocol,
                                               pipeshub_client: PipeshubClient) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        sim.ops({"op": "deliver", "key": "new-inbox", "from": "admin", "to": ["owner"], "subject": "IT new mail"},
                {"op": "deliver", "key": "new-filtered", "from": "ext:alerts@monitor.example", "to": ["owner"],
                 "subject": "IT new alert", "skip_inbox": ["owner"]})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        for key in ("new-inbox", "new-filtered"):
            await _wait_present(graph_provider, cid, sim.message(env.test_user, key)["id"], key)

    @pytest.mark.order(4)
    async def test_tc_gmail_ws_005_archive_moves(self, gmail_ws: dict[str, Any], graph_provider: GraphProviderProtocol,
                                                 pipeshub_client: PipeshubClient) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        msg_id = sim.message(env.test_user, "vendor")["id"]
        before = await graph_provider.get_record_by_external_id(cid, msg_id)
        sim.ops({"op": "archive", "user": "owner", "message": "vendor"})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        assert await graph_provider.record_belongs_to_external_group(cid, msg_id, f"{env.test_user}:OTHERS"), \
            "an archived message moves to OTHERS"
        sim.ops({"op": "label", "user": "owner", "message": "vendor", "add": ["INBOX"]})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        assert await graph_provider.record_belongs_to_external_group(cid, msg_id, f"{env.test_user}:INBOX"), \
            "moved back, it is in INBOX again"
        after = await graph_provider.get_record_by_external_id(cid, msg_id)
        assert after is not None and before is not None and after.id == before.id, "the move keeps the record"

    @pytest.mark.order(5)
    async def test_tc_gmail_ws_006_trash_and_untrash(self, gmail_ws: dict[str, Any],
                                                     graph_provider: GraphProviderProtocol,
                                                     pipeshub_client: PipeshubClient) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        msg = sim.message(env.test_user, "kickoff")
        sim.ops({"op": "trash", "user": "owner", "message": "kickoff"})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        await _wait_present(graph_provider, cid, msg["id"], "trashed message", present=False)
        for att in msg["attachments"]:
            await _wait_present(graph_provider, cid, f"{msg['id']}~{att['part']}", att["filename"], present=False)
        sim.ops({"op": "untrash", "user": "owner", "message": "kickoff"})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        await _wait_present(graph_provider, cid, msg["id"], "restored message")

    @pytest.mark.order(6)
    async def test_tc_gmail_ws_007_delete_and_spam(self, gmail_ws: dict[str, Any],
                                                   graph_provider: GraphProviderProtocol,
                                                   pipeshub_client: PipeshubClient) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        deleted = sim.message(env.test_user, "new-inbox")["id"]
        spammed = sim.message(env.test_user, "new-filtered")["id"]
        sim.ops({"op": "delete", "user": "owner", "message": "new-inbox"},
                {"op": "spam", "user": "owner", "message": "new-filtered"})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        await _wait_present(graph_provider, cid, deleted, "deleted message", present=False)
        await _wait_present(graph_provider, cid, spammed, "message marked as spam", present=False)

    @pytest.mark.order(7)
    async def test_tc_gmail_ws_008_expired_history(self, gmail_ws: dict[str, Any],
                                                   graph_provider: GraphProviderProtocol,
                                                   pipeshub_client: PipeshubClient) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        gone = sim.message(env.test_user, "kickoff-re")["id"]
        sim.ops({"op": "delete", "user": "owner", "message": "kickoff-re"},
                {"op": "deliver", "key": "during-gap", "from": "admin", "to": ["owner"], "subject": "IT during gap"},
                {"op": "expire_history", "user": "owner"})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        await _wait_present(graph_provider, cid, sim.message(env.test_user, "during-gap")["id"], "mail sent meanwhile")
        await _wait_present(graph_provider, cid, gone, "mail deleted meanwhile", present=False)

    @pytest.mark.order(8)
    async def test_tc_gmail_ws_009_received_date_filter(self, gmail_ws: dict[str, Any],
                                                        graph_provider: GraphProviderProtocol,
                                                        pipeshub_client: PipeshubClient) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        old = sim.message(env.test_user, "old")["id"]
        inbox = f"{env.test_user}:INBOX"
        start = int(time.time() * 1000) - 90 * DAY_MS
        narrowed = connector_filters(sync_values={"received_date": {
            "operator": "is_after", "type": "datetime", "value": {"start": start, "end": None}}})
        # A filter change starts a full sync itself; restarting the connector on top of it would cancel that
        # sync or be refused while it runs. The full sync unlinks the mail it no longer reads and keeps the node,
        # so out of scope is out of its group.
        await _filter_sync(pipeshub_client, graph_provider, cid, narrowed, "narrowed Received Date filter")
        await _wait_in_group(graph_provider, cid, old, inbox, "mail older than the filter", member=False)
        await _wait_in_group(graph_provider, cid, sim.message(env.test_user, "vendor")["id"], inbox, "recent mail")
        # The save merges filters per section, so a body without "sync" would keep the narrowed filter.
        cleared = {**connector_filters(), "sync": {"values": {}}}
        await _filter_sync(pipeshub_client, graph_provider, cid, cleared, "cleared Received Date filter")
        await _wait_in_group(graph_provider, cid, old, inbox, "old mail once the filter is cleared")

    @pytest.mark.order(9)
    async def test_tc_gmail_ws_010_streaming(self, gmail_ws: dict[str, Any], graph_provider: GraphProviderProtocol,
                                             pipeshub_client: PipeshubClient) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        msg = sim.message(env.test_user, "vendor")
        mail = await graph_provider.get_record_by_external_id(cid, msg["id"])
        assert mail is not None
        attachment = await graph_provider.get_record_by_external_id(cid, f"{msg['id']}~1")
        assert attachment is not None
        owner = log_in_existing_user(pipeshub_client, gmail_ws["user_ids"][env.test_user], env.test_user)
        try:
            resp = _stream_as(pipeshub_client, owner, mail.id)
            assert resp.status_code == 200 and b"Body of vendor" in resp.content, \
                f"a message streams its body: HTTP {resp.status_code}"
            resp = _stream_as(pipeshub_client, owner, attachment.id)
            want = sim.attachment_bytes(msg["owner_id"], msg["id"], "1")
            assert resp.status_code == 200 and resp.content == want, \
                f"an attachment streams its exact bytes: HTTP {resp.status_code}"
        finally:
            log_out_existing_user(pipeshub_client, owner)

    @pytest.mark.order(10)
    async def test_tc_gmail_ws_011_suspended_user(self, gmail_ws: dict[str, Any],
                                                  graph_provider: GraphProviderProtocol,
                                                  pipeshub_client: PipeshubClient) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        record = await graph_provider.get_record_by_external_id(cid, sim.message(env.second_user, "for-second")["id"])
        assert record is not None
        sim.ops({"op": "suspend_user", "user": "second"})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        second = log_in_existing_user(pipeshub_client, gmail_ws["user_ids"][env.second_user], env.second_user)
        try:
            wait_for_record_access(second, record.id, expect_access=False,
                                   description="their mail while suspended in Google Workspace")
            sim.ops({"op": "unsuspend_user", "user": "second"})
            await sync_and_wait(pipeshub_client, graph_provider, cid)
            wait_for_record_access(second, record.id, expect_access=True, description="their mail once reinstated")
        finally:
            log_out_existing_user(pipeshub_client, second)

    @pytest.mark.order(11)
    async def test_tc_gmail_ws_012_delegation_removed(self, gmail_ws: dict[str, Any],
                                                      graph_provider: GraphProviderProtocol,
                                                      pipeshub_client: PipeshubClient) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        before = await graph_provider.count_records(cid)
        sim.change_app(dwd_disabled=True)
        try:
            pipeshub_client.resync_connector(cid, full_sync=True)
            await wait_for_sync_completion(pipeshub_client, graph_provider, cid, timeout=300)
            await graph_provider.assert_record_count_unchanged(cid, before)
            outcome = pipeshub_client.get_connector(cid).get("lastSyncOutcome")
            if outcome is not None:
                assert str(outcome).lower() != "succeeded", "a sync without delegation does not report success"
        finally:
            sim.change_app(dwd_disabled=False)
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        await _wait_present(graph_provider, cid, sim.message(env.test_user, "vendor")["id"], "mail after recovery")
