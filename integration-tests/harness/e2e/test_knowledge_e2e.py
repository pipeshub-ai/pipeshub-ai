"""MCP-E2E KN-FS: the knowledge tools reach every executor through the full stack, with the reader's access.

Two users, two knowledge bases, both attached to one agent per executor: ``private`` is the admin's alone and
``shared`` is the admin's with the second user as a reader. Each holds one record uploaded through the product and
indexed by its indexing service (the stack's graph, Qdrant, local blob storage; embeddings are the scripted
model's). A turn searches for the words both records share: the admin's run reads both records, the second user's
run reads the shared one only, on the chat loop and on each engine, on Neo4j (ST-1) and ArangoDB (ST-5).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest
from lane import StackError, wait_until
from mcp_rows import EXECUTORS, Chat, executors_here, second_user, tool
from plan import ProcessEnv

if TYPE_CHECKING:
    from conftest import Stacks
    from lane import Stack
    from mcp_rows import Member, Turn

Json = dict[str, Any]
pytestmark = pytest.mark.mcp_hub
STACKS = {"ST-1": "neo4j", "ST-5": "arangodb"}
QUERY = "quarterly ledger regions"
PRIVATE_MARKER, SHARED_MARKER = "ALPHA7319", "GAMMA2207"
RECORDS = {
    "private": f"# Quarterly ledger, private\n\nThe quarterly ledger of the regions, {PRIVATE_MARKER}: the south region closed at 800 units.\n",
    "shared": f"# Quarterly ledger, shared\n\nThe quarterly ledger of the regions, {SHARED_MARKER}: the north region closed at 300 units.\n",
}
SEARCH = ("tool search_internal_knowledge " + json.dumps({"query": QUERY})
          + " || knowledgegraph__search " + json.dumps({"query": QUERY}))
FETCH = "knowledgegraph__fetch_record"
CITED, FINALIZED = "x.pipeshub.citations.recorded", "x.pipeshub.answer.finalized"


@dataclass
class Library:
    stack: Stack
    bob: Member
    kbs: dict[str, str] = field(default_factory=dict[str, str])
    records: dict[str, str] = field(default_factory=dict[str, str])
    agents: dict[str, str] = field(default_factory=dict[str, str])


def _kb_with_record(stack: Stack, name: str, text: str) -> tuple[str, str]:
    api = stack.api
    kb = api.ok("POST", "/api/v1/knowledgeBase", {"kbName": f"MCPE2E {name}"})
    kb_id = str((kb or {}).get("id") or (kb or {}).get("_key") or ((kb or {}).get("knowledgeBase") or {}).get("id") or "")
    if not kb_id:
        raise StackError(f"the knowledge base create returned no id: {str(kb)[:300]}")
    events = stack.upload(f"/api/v1/knowledgeBase/{kb_id}/upload", f"ledger-{name}.md", text.encode())
    record = next((str(e.data.get("recordId")) for e in events if e.event == "file:succeeded"), None)
    if record is None:
        raise StackError(f"the upload reported no stored file: {[e.event for e in events]}")

    def indexed() -> bool:
        found = api.ok("GET", f"/api/v1/knowledgeBase/record/{record}") or {}
        return str((found.get("record") or {}).get("indexingStatus")) == "COMPLETED"

    wait_until(indexed, timeout=300, what=f"the {name} record to be indexed", every=3)
    return kb_id, record


@pytest.fixture(scope="module", params=list(STACKS))
def library(request: pytest.FixtureRequest, stacks: Stacks) -> Library:
    stack = stacks.get(request.param, mcp_hub=True)
    assert stack.env["DATA_STORE"] == STACKS[request.param]
    stack.set_process_env(ProcessEnv(engine_runs=True).env())
    made = Library(stack, second_user(stack))
    for name, text in RECORDS.items():
        made.kbs[name], made.records[name] = _kb_with_record(stack, name, text)
    stack.api.ok("POST", f"/api/v1/knowledgeBase/{made.kbs['shared']}/permissions",
                 {"userIds": [made.bob.user_id], "role": "READER"})
    for executor in executors_here():
        body: Json = {"name": f"MCPE2E knowledge {executor}", "description": "Reads the ledgers.",
                      "systemPrompt": "You are a careful analyst.", "shareWithOrg": True}
        if executor != "loop":
            body["engineProfile"] = {"engines": [EXECUTORS[executor][0]]}
        agent = stack.api.ok("POST", "/api/v1/agents/create", body)
        key = str(((agent or {}).get("agent") or agent or {}).get("_key") or "")
        stack.api.ok("PUT", f"/api/v1/agents/{key}", {"knowledge": [{"connectorId": kb} for kb in made.kbs.values()]})
        made.agents[executor] = key
    return made


def _executor(name: str) -> str:
    if name not in executors_here():
        pytest.skip(f"the {name} engine image is not on this host")
    return name


def _read(turn: Turn) -> str:
    """What the run's tools answered and what its answer says."""
    return "\n".join([*turn.tool_results(), turn.obs.answer])


def _citations(turn: Turn) -> str:
    return json.dumps([e.get("data") for e in turn.obs.of_type(CITED, FINALIZED)], default=str)


@pytest.mark.parametrize("executor", list(EXECUTORS))
def test_kn_fs1_the_owner_reads_both_records_and_the_answer_cites_one(library: Library, executor: str) -> None:
    lines = [SEARCH]
    if _executor(executor) != "loop":
        lines.append(tool(FETCH, record_ids=[library.records["private"]]))
    chat = Chat(library.stack, executor, library.agents[executor])
    turn = chat.send(*lines, answer="The ledgers close each quarter [s](ref1), and never [x](ref99).")
    assert turn.obs.terminal == "finished", turn.failure()
    read = _read(turn)
    assert PRIVATE_MARKER in read and SHARED_MARKER in read, read[:1500]
    if executor != "loop":
        # KN-FS-2: the whole record the run was shown, its blocks labelled for citation.
        assert "south region closed at 800" in turn.tool_results()[1] and "ref" in turn.tool_results()[1]
    # KN-FS-4: what the run read is journaled for citation, the cited ref resolves to a record of the run's, and
    # a ref the model invented is not kept.
    cited = _citations(turn)
    assert any(record in cited for record in library.records.values()), cited[:1200]
    (final,) = turn.obs.of_type(FINALIZED)
    assert "ref99" not in json.dumps(final["data"]), final["data"]
    assert final["data"].get("citations"), final["data"]


@pytest.mark.parametrize("executor", list(EXECUTORS))
def test_kn_fs3_the_reader_of_one_base_never_reads_the_other(library: Library, executor: str) -> None:
    chat = Chat(library.stack, _executor(executor), library.agents[executor], api=library.bob.api)
    lines = [SEARCH]
    if executor != "loop":
        # A record this user's run was never shown, asked for by its id.
        lines.append(tool(FETCH, record_ids=[library.records["private"]]))
    turn = chat.send(*lines)
    assert turn.obs.terminal == "finished", turn.failure()
    read = _read(turn) + _citations(turn)
    assert SHARED_MARKER in read, read[:1500]
    assert PRIVATE_MARKER not in read and "south region closed at 800" not in read
    assert library.records["private"] not in _citations(turn)
    if executor != "loop":
        assert "record_not_shown" in turn.tool_results()[1], turn.tool_results()[1][:300]


def test_kn_fs5_the_records_are_in_the_stacks_own_stores(library: Library) -> None:
    stack = library.stack
    qdrant = stack.runtime.container("qdrant")  # type: ignore[union-attr]
    points = subprocess.run(
        ["docker", "exec", stack.runtime.container("pipeshub-ai"), "python", "-c",  # type: ignore[union-attr]
         "import json,os,urllib.request as u;"
         "r=u.Request('http://qdrant:6333/collections',headers={'api-key':os.environ.get('QDRANT_API_KEY','')});"
         "names=[c['name'] for c in json.load(u.urlopen(r))['result']['collections']];"
         "print(json.dumps({n: json.load(u.urlopen(u.Request(f'http://qdrant:6333/collections/{n}',"
         "headers={'api-key':os.environ.get('QDRANT_API_KEY','')})))['result']['points_count'] for n in names}))"],
        check=True, capture_output=True, text=True).stdout
    assert qdrant and sum(json.loads(points).values()) >= len(RECORDS), points
    assert stack.env["DATA_STORE"] in STACKS.values()
