"""PERF-01 (F1.1-F1.3, F3.2): the caller's team ids, resolved by the real connectors service, on a large org and for 300 teams.

Given an org with many users (graph filler, ``PCC_PERF01_USERS``, default 20000), one user in two teams, and one user in 300 teams
When the ids are resolved the way Node does (``GET /api/v1/entity/user/team-ids``, scope ``team:ids:read``), and through Node's
     "shared with me" list
Then both ids come back in under 2 KB and 300 ms; all 301 ids (300 teams and All) come back for the big user, which is more than the
     200 index scans Mongo explodes for sort, and a chat shared with the 300th team is listed for that user.

Real Node, real connectors service, real graph (Neo4j or Arango, ``PCC_E2E_GRAPH``). Needs ``PCC_E2E_REAL_PYTHON=1``.
"""

from __future__ import annotations

import os
import statistics
import time

import pytest
import requests

from helper.collab_stack import chats, collab
from helper.collab_stack.identity import Actor, Directory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.collab_chats,
    pytest.mark.collab_stack,
    pytest.mark.collab_real_python,
    pytest.mark.usefixtures("flag_on_for_module"),
]

FILLER_USERS = int(os.environ.get("PCC_PERF01_USERS", "20000"))
BIG_TEAMS = 300
LATENCY_BUDGET_S = 0.3
TWO_TEAMS_BODY_LIMIT = 2048
STACK_KEEP_STATE = True


def team_ids(stack, who: Actor) -> tuple[list[str], int, float]:  # noqa: ANN001
    """(ids, body bytes, seconds) of one call, made as Node makes it."""
    token = Directory.service_token(who, ("team:ids:read",))
    started = time.perf_counter()
    resp = requests.get(f"{stack.python.connectors_url}/api/v1/entity/user/team-ids", headers={"Authorization": f"Bearer {token}"}, timeout=30)
    elapsed = time.perf_counter() - started
    assert resp.status_code == 200, resp.text[:300]
    return resp.json()["teamIds"], len(resp.content), elapsed


def median_latency(stack, who: Actor, runs: int = 7) -> float:  # noqa: ANN001
    team_ids(stack, who)  # warm the connection and the provider
    return statistics.median(team_ids(stack, who)[2] for _ in range(runs))


def create_team(api, creator: Actor, name: str, *members: Actor) -> str:  # noqa: ANN001
    body = {"name": name, "description": "perf-01", "userRoles": [{"userId": m.user_id, "role": "READER"} for m in members]}
    resp = api.post("/api/v1/teams", creator, json_body=body)
    assert resp.status_code in (200, 201), f"create team {name}: {resp.status_code} {resp.text[:300]}"
    data = resp.json()["data"]
    return data.get("id") or data["_key"]


@pytest.fixture(scope="module")
def world(stack):  # noqa: ANN001, ANN201
    api = stack.api
    small, big, owner = collab.fresh_actor(stack, "P1S"), collab.fresh_actor(stack, "P1B"), collab.fresh_actor(stack, "P1O")
    org_id = small.org_id
    stack.python.graph({"op": "bulk_users", "org_id": org_id, "count": FILLER_USERS, "prefix": "perf01"})
    small_teams = [create_team(api, small, f"perf01-small-{i}") for i in range(2)]
    # The last team has a second member, who may then share a chat with it (a person can only add teams they belong to).
    big_teams = [create_team(api, big, f"perf01-big-{i}", *([owner] if i == BIG_TEAMS - 1 else [])) for i in range(BIG_TEAMS)]
    return small, small_teams, big, big_teams, f"all_{org_id}", owner


def test_perf01_a_user_in_two_teams_gets_both_ids_in_a_small_body_fast(stack, world) -> None:  # noqa: ANN001
    small, small_teams, _big, _big_teams, all_team, _owner = world

    ids, size, _ = team_ids(stack, small)

    assert set(ids) == {*small_teams, all_team}, ids
    assert size < TWO_TEAMS_BODY_LIMIT, f"{size} bytes"
    assert median_latency(stack, small) < LATENCY_BUDGET_S


def test_perf01_a_user_in_300_teams_gets_every_id(stack, world) -> None:  # noqa: ANN001
    _small, _teams, big, big_teams, all_team, _owner = world

    ids, size, _ = team_ids(stack, big)

    assert len(ids) == len(set(ids)) == BIG_TEAMS + 1
    assert set(ids) == {*big_teams, all_team}
    assert size < 32 * 1024
    assert median_latency(stack, big) < LATENCY_BUDGET_S


def test_perf01_a_chat_shared_with_the_300th_team_is_listed_for_its_member(stack, api, world) -> None:  # noqa: ANN001
    _small, _teams, big, big_teams, _all, owner = world
    chat = chats.create_chat(api, owner)
    shared = collab.put(api, owner, chat, collab.team(big_teams[-1], "read"))
    assert shared.status_code == 200, shared.text[:300]

    listed = api.get(chats.CONVERSATIONS, big, params={"source": "shared"})

    assert listed.status_code == 200, listed.text[:300]
    assert chat in {c["_id"] for c in listed.json()["conversations"]}
    assert not [c for c in api.get(chats.CONVERSATIONS, stack.roster.stranger, params={"source": "shared"}).json()["conversations"] if c["_id"] == chat]
