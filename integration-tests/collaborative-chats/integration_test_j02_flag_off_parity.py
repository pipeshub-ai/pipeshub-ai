"""Journey J-02: flag-off parity.

Given the collaboration flags are off
When every existing chat, agent and project API is called as the owner, a recipient, a project member and a stranger
Then each behaves as the baseline (origin/main without the feature), except the documented deviations DV-1 to
DV-3 (PH-04) and the other changes PH-04 recorded, each asserted below

Owning phase: PH-04 (80-implementation-plan section 5).

The baseline is `stack/fixtures/j02_flag_off_baseline.json`: the same scripted calls (`helper/collab_stack/parity.py`)
played against origin/main at the last merge (BASELINE_COMMIT; first captured from the PH-00 tree 77fbd5618, re-captured
when origin/main was merged in) on a fresh database with the same seed.
This file replays them on the tree under test and
diffs. A difference that is not declared fails; a declared deviation that no longer differs fails too, so the list stays exact.
"""

from __future__ import annotations

import copy
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from helper.collab_stack import parity
from helper.collab_stack.fake_backend import Reply
from helper.collab_stack.node_api import DEFAULT_NODE_ROOT
from helper.collab_stack.parity import ACTOR_NAMES, CALLS, canon, diff
from helper.collab_stack.seeds import insert_session, team_row, user_row
from helper.collab_stack.stack import CollabStack

pytestmark = [pytest.mark.integration, pytest.mark.collab_chats, pytest.mark.collab_stack, pytest.mark.usefixtures("flag_off_for_module")]

# origin/main the branch was last merged with. The feature's own Node changes are absent from it, so its flag-off
# behaviour is what the flag-off tree must reproduce. PH-00 (77fbd5618) changed no Node source.
BASELINE_COMMIT = "2baf7310f"
NOT_CAPTURING = pytest.mark.skipif(os.environ.get("PCC_E2E_CAPTURE") == "write", reason="capturing the baseline")
NON_OWNERS = tuple(a for a in ACTOR_NAMES if a != "Owner")
CALL_IDS = [c.id for c in CALLS]
CONVERSATION_ROUTE_IDS = [i for i in CALL_IDS if i[0] in "CA" and not i.startswith("A8-missing")]


# ---- the baseline and the capture of the tree under test ---------------------------------------


@pytest.fixture(scope="module")
def baseline() -> dict[str, dict[str, Any]]:
    doc = parity.load_baseline()
    assert doc["meta"]["capturedFrom"].startswith(BASELINE_COMMIT), f"baseline is not from {BASELINE_COMMIT}: {doc['meta']['capturedFrom']}"
    return doc["calls"]


@pytest.fixture(scope="module")
def current(stack: CollabStack, flag_off_for_module: None) -> dict[str, dict[str, Any]]:
    return parity.capture(stack)


# ---- declared deviations -----------------------------------------------------------------------


@dataclass(frozen=True)
class Deviation:
    """Pairs of (call, actor) that differ from the baseline on purpose, and what each side must look like."""

    name: str
    why: str
    pairs: frozenset[tuple[str, str]]
    check: Callable[[str, str, dict[str, Any], dict[str, Any], dict[str, dict[str, Any]]], None]


def pairs(calls: tuple[str, ...], actors: tuple[str, ...]) -> frozenset[tuple[str, str]]:
    return frozenset((c, a) for c in calls for a in actors)


def has_event(entry: dict[str, Any], name: str) -> bool:
    return entry["contentType"] == "text/event-stream" and any(e["event"] == name for e in entry["body"])


def check_dv1(call: str, actor: str, base: dict, cur: dict, _cur_all: dict) -> None:
    # Before: 200 with an event stream that names the owner's conversation and then fails with RUN_ERROR.
    assert base["status"] == 200 and base["contentType"] == "text/event-stream", f"{call}/{actor} baseline"
    assert has_event(base, "RUN_ERROR")
    assert not base["aiRequests"], "the baseline already reached the AI backend"
    # Now: a JSON 404 before any SSE byte, nothing leaked and nothing sent to the AI backend.
    assert cur["status"] == 404 and cur["contentType"] == "application/json", f"{call}/{actor} current"
    assert cur["body"]["error"]["code"] == "CONVERSATION_NOT_FOUND"
    assert cur["aiRequests"] == []
    assert cur["after"] == base["after"], "a denied stream changed state"


def check_dv3(call: str, actor: str, base: dict, cur: dict, _cur_all: dict) -> None:
    # Before: an idempotent 200 for a non-owner (or a missing id); a shared recipient even soft-deleted the owner's chat.
    assert base["status"] == 200 and base["body"]["message"] == "Conversation deleted successfully"
    if call == "A8":
        deleted = base["after"]["agent"]["isDeleted"]
        assert deleted == (actor in ("Reader", "Writer")), f"baseline: recipients deleted the owner's agent chat (got {deleted} for {actor})"
    # Now: 404, and the owner's chats are untouched.
    assert cur["status"] == 404 and cur["body"]["error"]["code"] == "CONVERSATION_NOT_FOUND"
    assert cur["after"]["agent"]["isDeleted"] is False and cur["after"]["agent-solo"]["isDeleted"] is False
    assert cur["after"]["sessionCount"] == base["after"]["sessionCount"]


def check_detail_filters(call: str, actor: str, base: dict, cur: dict, cur_all: dict) -> None:
    # Before: ?search / ?shared on the detail route filtered the lookup, so an existing chat was a 404.
    assert base["status"] == 404
    # Now: the same chat as the unfiltered request.
    assert cur["status"] == 200
    assert cur["body"]["conversation"]["messages"] == cur_all["C5"][actor]["body"]["conversation"]["messages"]


def check_internal(call: str, actor: str, base: dict, cur: dict, _cur_all: dict) -> None:
    # Before: validation (400) or the lookup (404) ran first; now the token's user is checked first.
    assert base["status"] in (400, 404)
    assert cur["status"] == 401 and cur["body"]["error"]["message"] == "This account is disabled"
    assert cur["after"]["sessionCount"] == base["after"]["sessionCount"]


def check_team_lookup(call: str, actor: str, base: dict, cur: dict, _cur_all: dict) -> None:
    # One lookup per request, and nothing else differs: team rows in projects are the only new input.
    assert (base["teamLookups"], cur["teamLookups"]) == (0, 1)
    assert diff(canon({**base, "teamLookups": 0}), canon({**cur, "teamLookups": 0})) == [], f"{call}/{actor} also differs elsewhere"


def check_first_send_records_used(call: str, actor: str, base: dict, cur: dict, _cur_all: dict) -> None:
    # Before: the first send relayed the AI backend's final frame as it was. Now the one turn pump ends every stream,
    # and its RUN_FINISHED carries the follow-up shape: `recordsUsed` (the citation count) beside `conversation`.
    stripped = copy.deepcopy(cur)
    finished = [e["data"]["result"] for e in stripped["body"] if e["event"] == "RUN_FINISHED" and "result" in e["data"] and "conversation" in e["data"]["result"]]
    assert len(finished) == 1, f"{call}/{actor}: one final frame with the conversation"
    assert finished[0].pop("recordsUsed") == finished[0]["meta"].pop("recordsUsed") == 0
    assert diff(canon(base), canon(stripped)) == [], f"{call}/{actor} also differs elsewhere"


DEVIATIONS: tuple[Deviation, ...] = (
    Deviation(
        "DV-1 stream denial is a JSON 404",
        "PH-04 PR-4a: the guard runs before the SSE header; the client no longer gets a 200 stream that fails with RUN_ERROR.",
        pairs(("C3", "C4", "C11", "A2", "A3", "A4"), NON_OWNERS),
        check_dv1,
    ),
    Deviation(
        "DV-3 delete of a missing or foreign agent chat is a 404",
        "PH-04 SEC-04: was an idempotent 200 (and, for a shared recipient, a real delete of the owner's chat).",
        pairs(("A8",), NON_OWNERS) | pairs(("A8-missing",), ACTOR_NAMES),
        check_dv3,
    ),
    Deviation(
        "detail ignores ?search, ?date and ?shared",
        "PH-04 PR-4b: filters belong to the list routes; on detail they only produced false 404s.",
        pairs(("C5-filtered",), tuple(a for a in ACTOR_NAMES if a not in ("Stranger", "Outsider"))),
        check_detail_filters,
    ),
    Deviation(
        "internal routes answer 401 for a disabled user before validation",
        "PH-01 S4b / PH-04 PR-4c: scoped-user hydration runs before the body validator and the lookup.",
        pairs(("I1", "I2"), ("Disabled",)),
        check_internal,
    ),
    Deviation(
        "list and project reads ask the team directory once",
        "PH-03 PR-3.3 / PH-04 PR-4d: team membership now counts for project access on the shared list, the agent lists and the project reads (flag-off widening, consistent with computeRole).",
        pairs(("L2", "L6", "L7", "P1", "P2", "P3", "P4", "P5"), ACTOR_NAMES),
        check_team_lookup,
    ),
    Deviation(
        "a first-send stream ends with the follow-up frame shape",
        "PH-05 PR-5d: streamChat and streamAgentConversation now end through the turn pump, whose final frame adds `recordsUsed` and `meta.recordsUsed`; not listed in PH-05 'Flag off', additive, and no client reads it.",
        pairs(("N2", "N4", "N6"), ACTOR_NAMES),
        check_first_send_records_used,
    ),
)
DECLARED = {pair: dev for dev in DEVIATIONS for pair in dev.pairs}


# ---- tests -------------------------------------------------------------------------------------


@pytest.mark.collab_capture
def test_j02_capture_baseline(stack: CollabStack) -> None:
    """`PCC_E2E_CAPTURE=write` with the baseline API: records the fixture the other tests diff against."""
    if os.environ.get("PCC_E2E_CAPTURE") != "write":
        pytest.skip(
            f"set PCC_E2E_CAPTURE=write and PCC_E2E_NODE_ROOT=<{BASELINE_COMMIT} worktree>/backend/nodejs/apps to refresh the baseline"
        )
    root = Path(os.environ.get("PCC_E2E_NODE_ROOT", DEFAULT_NODE_ROOT))
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    assert commit.startswith(BASELINE_COMMIT), f"refusing to record a baseline from {commit}: the tree must be {BASELINE_COMMIT}"
    parity.write_baseline(parity.capture(stack), commit)


@NOT_CAPTURING
@pytest.mark.parametrize("call_id", CALL_IDS)
def test_j02_matches_baseline(call_id: str, baseline: dict, current: dict) -> None:
    """Every call, as every actor, equals the baseline capture unless it is a declared deviation (which is asserted instead)."""
    unexplained: list[str] = []
    for actor in baseline[call_id]:
        base, cur = baseline[call_id][actor], current[call_id][actor]
        deviation = DECLARED.get((call_id, actor))
        if deviation is None:
            unexplained += [f"{actor}: {line}" for line in diff(canon(base), canon(cur))]
            continue
        assert base != cur, f"{deviation.name}: {call_id}/{actor} no longer differs from the baseline; remove it from DEVIATIONS"
        deviation.check(call_id, actor, base, cur, current)
    assert not unexplained, f"{call_id} differs from the baseline:\n  " + "\n  ".join(unexplained[:25])


@NOT_CAPTURING
def test_j02_baseline_and_current_cover_the_same_calls(baseline: dict, current: dict) -> None:
    assert set(baseline) == set(current) == set(CALL_IDS)
    for call_id in CALL_IDS:
        assert set(baseline[call_id]) == set(current[call_id])
    assert all(pair[0] in baseline and pair[1] in baseline[pair[0]] for pair in DECLARED), "a declared deviation names a pair that was not captured"


@NOT_CAPTURING
def test_j02_denials_use_the_unified_conversation_not_found(current: dict) -> None:
    """Every 404 on a conversation route says CONVERSATION_NOT_FOUND with one text (no existence oracle between routes)."""
    seen = 0
    for call_id in CONVERSATION_ROUTE_IDS + ["A8-missing"]:
        for actor, entry in current[call_id].items():
            if entry["status"] == 404:
                seen += 1
                err = entry["body"]["error"]
                assert (err["code"], err["message"]) == ("CONVERSATION_NOT_FOUND", "Conversation not found"), f"{call_id}/{actor}: {err}"
    assert seen > 50


@NOT_CAPTURING
def test_j02_dv2_project_viewer_cancel_was_already_denied(baseline: dict, current: dict) -> None:
    """PH-04 recorded DV-2 (a project viewer could cancel an agent run). The baseline tree already answered 404, so nothing changed."""
    for actor in ("ProjectViewer", "ProjectEditor"):
        assert baseline["A5"][actor]["status"] == current["A5"][actor]["status"] == 404
        assert baseline["A5"][actor]["aiRequests"] == current["A5"][actor]["aiRequests"] == []
    assert baseline["A5"]["Owner"]["status"] == current["A5"]["Owner"]["status"] == 200


@NOT_CAPTURING
def test_j02_accounting(baseline: dict, current: dict) -> None:
    """How many (call, actor) pairs were compared, how many matched and how many are declared deviations."""
    total = sum(len(v) for v in baseline.values())
    equal = sum(1 for c, v in baseline.items() for a in v if (c, a) not in DECLARED and not diff(canon(v[a]), canon(current[c][a])))
    print(f"J-02: {total} pairs, {equal} identical, {len(DECLARED)} declared deviations in {len(DEVIATIONS)} groups")
    assert equal + len(DECLARED) == total


# ---- flag-off behaviour that the baseline cannot show (seeds it cannot read) -------------


@NOT_CAPTURING
def test_j02_team_rows_are_ignored_and_write_rows_read_as_read(stack: CollabStack, api, fake, roster) -> None:  # noqa: ANN001
    """SEC-19: with the flag off a team row grants nothing (and costs no team lookup) and a `write` row is a read row."""
    team = fake.add_team(roster.owner.org_id, "sharers", {roster.team_writer.user_id: "WRITER", roster.team_reader.user_id: "READER"})
    chat = insert_session(
        stack.db,
        "teamed",
        roster.owner,
        shared_with=[team_row(team.team_id, "write"), user_row(roster.write_recipient, "write", principal_type=True)],
    )

    for member in (roster.team_writer, roster.team_reader):
        assert api.get(f"/api/v1/conversations/{chat.sid}", member).status_code == 404
        assert api.post(f"/api/v1/conversations/{chat.sid}/messages", member, json_body={"query": "hi", "chatMode": "quick"}).status_code == 404
    assert not fake.requests_for("team_ids"), "a chat team row caused a team lookup with the flag off"

    writer = roster.write_recipient
    assert api.get(f"/api/v1/conversations/{chat.sid}", writer).status_code == 200
    assert api.post(f"/api/v1/conversations/{chat.sid}/messages", writer, json_body={"query": "hi", "chatMode": "quick"}).status_code == 404
    assert not fake.requests_for("chat")


@NOT_CAPTURING
def test_j02_flag_off_ai_payload_for_an_owner_turn(stack: CollabStack, api, fake, roster) -> None:  # noqa: ANN001
    """The request the owner's follow-up sends to the AI backend is what the baseline sent (the baseline compares it too; this pins the shape)."""
    chat = insert_session(stack.db, "payload", roster.owner)
    fake.on("chat", Reply({"answer": "ok", "citations": [], "confidence": "High", "reason": "", "answerMatchType": "x", "documentIndexes": []}))

    resp = api.post(f"/api/v1/conversations/{chat.sid}/messages", roster.owner, json_body={"query": "next", "chatMode": "quick"})

    assert resp.status_code == 200, resp.text[:300]
    body = fake.requests_for("chat")[0].body
    assert body["conversationId"] == chat.sid and body["query"] == "next"
    assert [t["content"] for t in body["previousConversations"]] == ["question payload", "answer payload"]
