"""Looking up a Slack permalink finds the record that holds that message.

The Slack Workspace connector stores messages posted within a few minutes of
each other as one "burst" record. A thread's replies are stored the same way,
in records that belong to the thread's own group (``thread_<channel>_<thread_ts>``)
rather than to the channel. Here the real connector syncs a fake workspace, and
the resulting records are handed to the real ``RecordResolver`` (the code behind
the knowledge-graph ``lookup_record`` tool and the lookup API). The graph stand-in
answers ``find_slack_burst_record_by_ts`` with the same match the Neo4j and
ArangoDB providers use: same connector, same group id, ``startTs <= ts <= endTs``,
and only for records that hold more than one message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

import pytest
from slack_behaviour_fakes import DOMAIN, SlackWorkspace, ts_minutes_ago
from slack_behaviour_setup import (
    ALICE,
    BOB,
    CAROL,
    GENERAL,
    WORKSPACE_CONNECTOR_ID,
    standard_workspace,
    workspace_connector,
)

from app.agents.actions.knowledge_graph.catalog import ConnectorCatalog, ConnectorInfo
from app.agents.actions.knowledge_graph.resolver import RecordResolver

if TYPE_CHECKING:
    from app.models.entities import MessageRecord


class SyncedGraph:
    """Graph lookups over the records the connector just wrote."""

    def __init__(self, records: list[MessageRecord]) -> None:
        self.records = records

    async def find_slack_burst_record_by_ts(
        self, connector_id: str, channel_id: str, ts: str, transaction: Optional[str] = None,
    ) -> Optional[MessageRecord]:
        for r in self.records:
            if (
                r.connector_id == connector_id
                and r.external_record_group_id == channel_id
                and r.start_ts
                and r.end_ts
                and r.start_ts <= ts <= r.end_ts
                and r.start_ts != r.end_ts
            ):
                return r
        return None

    async def get_record_by_weburl(
        self, weburl: str, org_id: Optional[str] = None, transaction: Optional[str] = None,
    ) -> Optional[MessageRecord]:
        return next((r for r in self.records if r.weburl == weburl), None)

    async def get_knowledge_hub_node_access(self, node_id: str, **_: object) -> dict[str, Any]:
        return {"id": node_id}


def permalink(channel: str, ts: str, thread_ts: Optional[str] = None) -> str:
    """The link Slack's "Copy link" gives for a message (a reply also names its thread)."""
    url = f"https://{DOMAIN}.slack.com/archives/{channel}/p{ts.replace('.', '')}"
    return f"{url}?thread_ts={thread_ts}&cid={channel}" if thread_ts else url


def resolver_over(records: list[MessageRecord]) -> RecordResolver:
    return RecordResolver(
        graph_provider=SyncedGraph(records),  # type: ignore[arg-type]
        catalog=ConnectorCatalog(
            [ConnectorInfo(id=WORKSPACE_CONNECTOR_ID, name="Slack", type="SLACK WORKSPACE")]
        ),
        org_id="org-1",
        user_id="user-1",
        user_key="user-key-1",
        folder_mime_types=[],
    )


def holding(store, ts: str) -> MessageRecord:
    """The one stored record whose blocks include the message ``ts``."""
    found = [r for r in store.messages() if ts in {b.source_id for b in r.block_containers.blocks}]
    assert len(found) == 1, f"expected exactly one record holding {ts}, got {len(found)}"
    return found[0]


@pytest.fixture
def workspace(slack: SlackWorkspace) -> SlackWorkspace:
    return standard_workspace(slack)


class TestPermalinkLookup:
    async def test_a_reply_in_the_middle_of_a_thread_burst_resolves_to_that_burst(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(300)
        workspace.post(GENERAL, parent, ALICE, "deploy is failing on staging")
        replies = [ts_minutes_ago(m) for m in (299, 298, 297)]
        for ts, user in zip(replies, (BOB, CAROL, ALICE)):
            workspace.reply(GENERAL, parent, ts, user, f"looking {ts}")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        middle = replies[1]
        burst = holding(store, middle)
        assert burst.external_record_group_id == f"thread_{GENERAL}_{parent}"
        assert burst.start_ts != middle  # not the first message, so its own URL is not the record's

        result = await resolver_over(store.messages()).resolve_many([permalink(GENERAL, middle, parent)])

        assert [m.id for m in result.matches] == [burst.id]
        assert result.not_found_identifiers == []

    async def test_a_message_in_the_middle_of_a_channel_burst_still_resolves(self, workspace, store, checkpoints) -> None:
        stamps = [ts_minutes_ago(m) for m in (120, 119, 118)]
        for ts, user in zip(stamps, (ALICE, BOB, CAROL)):
            workspace.post(GENERAL, ts, user, f"standup {ts}")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        burst = holding(store, stamps[1])
        assert burst.external_record_group_id == GENERAL

        result = await resolver_over(store.messages()).resolve_many([permalink(GENERAL, stamps[1])])

        assert [m.id for m in result.matches] == [burst.id]

    async def test_a_reply_link_to_a_message_that_was_never_synced_is_not_found(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(300)
        workspace.post(GENERAL, parent, ALICE, "deploy is failing on staging")
        for ts in (ts_minutes_ago(299), ts_minutes_ago(297)):
            workspace.reply(GENERAL, parent, ts, BOB, f"looking {ts}")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        other_thread = ts_minutes_ago(500)
        result = await resolver_over(store.messages()).resolve_many(
            [permalink(GENERAL, ts_minutes_ago(298), other_thread)]
        )

        assert result.matches == []
