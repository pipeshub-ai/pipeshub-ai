"""PH12-02 (G-cum 4): every collaborative-chats migration runs twice and the second run writes nothing; disabling leaves data readable.

Given a database and a graph holding legacy data (shared chats with legacy rows, a frozen `conversations` copy, agents without a
handle, a legacy READER edge onto a chat attachment)
When the migrations run on boot (chat_sessions_v1, acl_version_v1, chat_collaborators_v1 in Node; agent_handles_v1 and
     chat_grant_edges_cleanup_v1 in the query/connectors Python), boot again with their flags set, and boot after deleting the flags
Then the first run migrates, the second run is skipped, the re-run after deleting the flags leaves Mongo and the graph exactly as they
     were (no document or agent rewritten), and with the feature flag turned off the migrated chats are still readable.

Real Node, real connectors service and the lane's graph (Neo4j or Arango). Needs ``PCC_E2E_REAL_PYTHON=1``. The Node half alone is
also played with the fake Python services in ``integration_test_ph03_migrations_and_acl.py``, whose legacy world this reuses.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from helper.collab_stack import chats
from helper.collab_stack.identity import stable_oid
from helper.collab_stack.real_python import upload_attachment
from helper.collab_stack.stack import CHAT_MIGRATION_FLAGS, CollabStack
from integration_test_ph03_migrations_and_acl import reboot_with_legacy_data, snapshot

pytestmark = [
    pytest.mark.integration,
    pytest.mark.collab_chats,
    pytest.mark.collab_stack,
    pytest.mark.collab_real_python,
    pytest.mark.usefixtures("flag_off_for_module"),
]

STACK_KEEP_STATE = True
PYTHON_FLAGS = ("/migrations/agent_handles_v1", "/migrations/chat_grant_edges_cleanup_v1")
LEGACY_AGENTS = 3


def wait_for_python_flags(stack: CollabStack, timeout: float = 150) -> None:
    from helper.collab_stack.infra import wait_until

    wait_until(lambda: all(stack.kv_get(k) for k in PYTHON_FLAGS), timeout, interval=1, message="the Python migrations to write their completion flags")


def graph_state(stack: CollabStack, org_id: str, record_id: str) -> dict[str, Any]:
    agents, edges = stack.python.graph({"op": "agents", "org_id": org_id}, {"op": "reader_edges", "record_id": record_id})  # type: ignore[union-attr]
    return {"agents": sorted(agents, key=lambda a: a["id"]), "edges": sorted(edges, key=lambda e: (e["role"] or "", str(e["from"])))}


def boot_both(stack: CollabStack, *, forget: bool) -> None:
    """Restart Node and the connectors service, optionally with every migration flag deleted first.

    The connectors service comes up first: once it has written the deployment settings, Node's health route asks it about the graph
    and the vector store, and answers unhealthy while it is down."""
    assert stack.node is not None and stack.python is not None and stack.python.connectors is not None
    stack.node.stop()
    stack.python.connectors.stop()
    if forget:
        stack.forget_chat_migrations()
        stack.kv_delete(*PYTHON_FLAGS)
    stack.python.connectors.start()
    stack.node.start()
    stack.wait_for_chat_migrations()
    wait_for_python_flags(stack)


@pytest.fixture(scope="module")
def migrated(stack: CollabStack, flag_off_for_module: None) -> dict[str, Any]:
    """First run: legacy data in Mongo and the graph, every flag deleted, both services restarted."""
    assert stack.python is not None and stack.roster is not None
    roster = stack.roster
    org_id = roster.owner.org_id
    agent_ids = stack.python.graph({"op": "legacy_agents", "org_id": org_id, "count": LEGACY_AGENTS})[0]["ids"]
    record_id = upload_attachment(stack.node.base_url, roster.write_recipient)  # type: ignore[union-attr]
    [reader_key] = stack.python.graph({"op": "user_key", "user_id": roster.read_recipient.user_id})
    stack.python.graph({"op": "reader_edge", "user_key": reader_key, "record_id": record_id})
    before = graph_state(stack, org_id, record_id)

    ids = reboot_with_legacy_data(stack)
    stack.python.connectors.stop()  # type: ignore[union-attr]
    stack.kv_delete(*PYTHON_FLAGS)
    stack.python.connectors.start()  # type: ignore[union-attr]
    wait_for_python_flags(stack)
    return {
        "ids": ids,
        "org_id": org_id,
        "record_id": record_id,
        "agent_ids": agent_ids,
        "graph_before": before,
        "graph": graph_state(stack, org_id, record_id),
        "mongo": snapshot(stack),
        "flags": {k: stack.kv_get(k) for k in (*CHAT_MIGRATION_FLAGS, *PYTHON_FLAGS)},
    }


def test_ph12_02_the_first_run_migrates_mongo_and_the_graph(stack: CollabStack, migrated: dict[str, Any]) -> None:
    sessions = json.loads(migrated["flags"][CHAT_MIGRATION_FLAGS[0]])
    assert sessions["conversationsMigrated"] is True
    collaborators = json.loads(migrated["flags"][CHAT_MIGRATION_FLAGS[2]])
    assert collaborators["normalized"] > 0 and collaborators["errored"] == 0

    before = {a["id"]: a for a in migrated["graph_before"]["agents"]}
    assert all(not before[i].get("handle") for i in migrated["agent_ids"]), "the seeded agents start without a handle"
    after = {a["id"]: a for a in migrated["graph"]["agents"]}
    handles = sorted(after[i]["handle"] for i in migrated["agent_ids"])
    assert handles == ["legacy-agent", "legacy-agent-2", "legacy-agent-3"], handles

    roles_before = sorted(e["role"] for e in migrated["graph_before"]["edges"])
    roles_after = sorted(e["role"] for e in migrated["graph"]["edges"])
    assert roles_before == ["OWNER", "READER"] and roles_after == ["OWNER"], "the legacy READER edge on the attachment is removed, the owner's stays"


def test_ph12_02_the_second_run_is_skipped_and_writes_nothing(stack: CollabStack, migrated: dict[str, Any]) -> None:
    boot_both(stack, forget=False)

    assert snapshot(stack) == migrated["mongo"]
    assert graph_state(stack, migrated["org_id"], migrated["record_id"]) == migrated["graph"]
    assert {k: stack.kv_get(k) for k in (*CHAT_MIGRATION_FLAGS, *PYTHON_FLAGS)} == migrated["flags"], "a skipped migration leaves its flag alone"


def test_ph12_02_a_rerun_after_deleting_the_flags_changes_nothing(stack: CollabStack, migrated: dict[str, Any]) -> None:
    boot_both(stack, forget=True)

    assert snapshot(stack) == migrated["mongo"], "re-running the Node migrations rewrote migrated data"
    after = graph_state(stack, migrated["org_id"], migrated["record_id"])
    assert after["agents"] == migrated["graph"]["agents"], "re-running agent_handles_v1 touched an agent that already had a handle"
    assert after["edges"] == migrated["graph"]["edges"]
    assert all(stack.kv_get(k) for k in (*CHAT_MIGRATION_FLAGS, *PYTHON_FLAGS)), "every flag is written again"


def test_ph12_02_turning_the_feature_off_leaves_migrated_chats_readable(stack: CollabStack, api, flags, migrated: dict[str, Any]) -> None:  # noqa: ANN001
    roster, ids = stack.roster, migrated["ids"]
    assert roster is not None
    with flags.value(True):
        # With the feature on, the legacy writer was downgraded to a reader by chat_collaborators_v1: the chat opens, the write does not.
        assert chats.get_chat(api, roster.write_recipient, str(ids["mixed"])).status_code == 200
        assert chats.send_message(api, roster.write_recipient, str(ids["mixed"]), "hello").status_code == 403

    with flags.value(False):
        for who in (roster.owner, roster.read_recipient, roster.write_recipient):
            resp = chats.get_chat(api, who, str(ids["mixed"]))
            assert resp.status_code == 200, f"{who.name}: {resp.status_code} {resp.text[:200]}"
        listed = api.get(chats.CONVERSATIONS, roster.owner, params={"limit": 50})
        assert listed.status_code == 200 and str(ids["mixed"]) in {c["_id"] for c in listed.json()["conversations"]}
        assert chats.get_chat(api, roster.stranger, str(ids["not-shared"])).status_code == 404, "a chat that is not shared stays private"
    stack.db["chatSessions"].delete_many({"_id": stable_oid("flag-probe")})  # the flag helper's own probe chat
    assert snapshot(stack) == migrated["mongo"], "toggling the flag rewrites no stored chat"
