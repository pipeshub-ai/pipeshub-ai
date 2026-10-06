"""PH12-03 (AB-15 on the real graph): agent handles are unique per org, decided by the graph's own constraint.

Given several people creating agents at the same moment
When the names, or the explicit handles, are the same
Then exactly one create gets the plain handle; with a derived handle the others get ``-2``, ``-3``, ... (each handle once); with an
     explicit handle the others are refused with HANDLE_TAKEN and a free suggestion; the same handle in another org is free.

Real Node, real query service (agent create and the handle allocator), and the graph the lane runs on, Neo4j (unique constraint) or
Arango (unique index): run it on both with ``PCC_E2E_GRAPH``. Needs ``PCC_E2E_REAL_PYTHON=1``.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from helper.collab_stack import collab

pytestmark = [
    pytest.mark.integration,
    pytest.mark.collab_chats,
    pytest.mark.collab_stack,
    pytest.mark.collab_real_python,
]

AGENTS = "/api/v1/agents"
STACK_KEEP_STATE = True
CREATORS = 4


def create_agent(api, who, name: str, **body):  # noqa: ANN001, ANN003, ANN201
    return api.post(f"{AGENTS}/create", who, json_body={"name": name, "description": "perf", "instructions": "Be brief.", **body})


def create_together(api, creators, name: str, **body):  # noqa: ANN001, ANN003, ANN201
    with ThreadPoolExecutor(max_workers=len(creators)) as pool:
        return list(pool.map(lambda who: create_agent(api, who, name, **body), creators))


def handle_of(resp) -> str:  # noqa: ANN001
    assert resp.status_code in (200, 201), resp.text[:400]
    return resp.json()["agent"]["handle"]


@pytest.fixture
def creators(stack):  # noqa: ANN001, ANN201
    return [collab.fresh_actor(stack, f"AH{i}") for i in range(CREATORS)]


def test_ph12_03_concurrent_creates_with_the_same_name_each_get_a_distinct_handle(stack, api, creators, graph_db) -> None:  # noqa: ANN001
    name = f"Race {graph_db}"

    responses = create_together(api, creators, name)

    handles = sorted(handle_of(r) for r in responses)
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    assert len(set(handles)) == CREATORS, handles
    assert handles.count(base) == 1, f"exactly one create wins the plain handle: {handles}"
    assert all(h == base or re.fullmatch(rf"{re.escape(base)}-\d+", h) for h in handles), handles
    assert f"{base}-2" in handles, "a loser retries as -2"


def test_ph12_03_concurrent_creates_with_one_explicit_handle_have_one_winner(stack, api, creators) -> None:  # noqa: ANN001
    responses = create_together(api, creators, "Explicit", handle="explicit-race")

    won = [r for r in responses if r.status_code in (200, 201)]
    lost = [r for r in responses if r.status_code not in (200, 201)]
    assert len(won) == 1, [(r.status_code, r.text[:120]) for r in responses]
    assert handle_of(won[0]) == "explicit-race"
    for refused in lost:
        error = refused.json()["error"]
        assert refused.status_code == 409 and error["code"] == "HANDLE_TAKEN", refused.text[:300]
        assert re.fullmatch(r"explicit-race-\d+", (error.get("details") or {}).get("suggestion", "")), error


def test_ph12_03_the_same_handle_in_another_org_is_free(stack, api, creators) -> None:  # noqa: ANN001
    assert create_agent(api, creators[0], "Per org", handle="per-org-handle").status_code in (200, 201)
    outsider = stack.roster.other_org

    resp = create_agent(api, outsider, "Per org", handle="per-org-handle")

    assert handle_of(resp) == "per-org-handle"
    again = create_agent(api, creators[1], "Per org", handle="per-org-handle")
    assert again.status_code == 409
