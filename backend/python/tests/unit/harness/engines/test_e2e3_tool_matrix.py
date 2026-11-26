"""E2E3: every engine available crossed with every tool family, on real components (``impl/E2E3-tool-matrix.md``).

Each cell is one durable engine run on E1's real lane (Node's journal server, the real egress proxy, E3 in front of
the scripted model, the engine's release image under the Docker broker), assembled by the production
``PipesHubEngineAssembler`` over the lane's own Neo4j (``matrix/``). The scripted model makes the tool calls the
cell's prompt names, so every call is deterministic and nothing is spent. Every cell holds its engine's turn once
while the sandbox is live and runs the confinement checks (CF, NP) in it.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
import pytest_asyncio

from app import edition_config
from harness_core.testing.sandbox.mcp_runner import SECRET_NAME
from harness_core.tools.call_tool import CALL_TOOL, SEARCH_TOOLS
from tests.unit.harness.engines.matrix.assembly import (
    JIRA_READ,
    JIRA_WRITE,
    matrix_spec,
    mcp_tool,
)
from tests.unit.harness.engines.matrix.cells import (
    approve_all,
    assert_confined_cell,
    checks,
    tool,
)
from tests.unit.harness.engines.matrix.cells import engine_kind as _engine
from tests.unit.harness.engines.matrix.fixtures import (  # noqa: F401  (the lane's fixtures)
    echo_template_fixture,  # pyright: ignore[reportUnusedImport]
    matrix_fixture,  # pyright: ignore[reportUnusedImport]
)
from tests.unit.harness.engines.matrix.lane import (
    KILLED_LEASE_MS,
    MatrixLane,
    on_arango,
    open_interactions,
)
from tests.unit.harness.engines.matrix.org import QUERY
from tests.unit.harness.engines.matrix.services import JIRA_TOKEN, MCP_SECRET
from tests.unit.harness.engines.matrix.vector_stores import BACKENDS, on_vector_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator



pytestmark = [
    pytest.mark.docker,
    pytest.mark.timeout(1800, method="thread"),
    pytest.mark.asyncio(loop_scope="module"),
]

ENGINES = ("opencode", "codex", "claude_code")
FETCH = "knowledgegraph__fetch_record"
# The MCP tools as the gateway names them (``mcp_<server>_<tool>``).
ECHO, ENV_DIGEST, WHERE = "mcp_echo_echo", "mcp_echo_env_digest", "mcp_echo_where"
# What an engine adds around a gateway result that differs between two calls (Codex's wall time, the invocation id
# Codex and Claude Code show from the structured content): not part of what the tool answered.
_VOLATILE = re.compile(r"Wall time: [0-9.]+ seconds|inv_[0-9a-f-]+_[0-9]+")


def same_answer(first: str, first_id: str, second: str, second_id: str) -> bool:
    """Whether two tool results say the same thing once each one's own id is set aside."""
    return _VOLATILE.sub("", first.replace(first_id, "ID")) == _VOLATILE.sub("", second.replace(second_id, "ID"))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def matrix_arango(matrix: MatrixLane) -> AsyncIterator[MatrixLane]:
    """E2E4 AR: the lane over an ArangoDB of its own, seeded with the same org."""
    async with on_arango(matrix) as lane:
        yield lane


@pytest_asyncio.fixture(scope="module", loop_scope="module", params=list(BACKENDS))
async def matrix_vectors(request: pytest.FixtureRequest, matrix: MatrixLane) -> AsyncIterator[MatrixLane]:
    """C3.25 (KN-10): the lane over a real OpenSearch or Redis vector store of its own, seeded with the same blocks."""
    async with on_vector_store(matrix, request.param) as lane:
        yield lane


@pytest.mark.parametrize("engine", ENGINES)
async def test_kn_knowledge_tools_on_the_real_graph(matrix: MatrixLane, engine: str) -> None:
    await knowledge_cell(matrix, engine, "KN")


@pytest.mark.parametrize("engine", ENGINES)
async def test_ar_knowledge_tools_on_arangodb(matrix_arango: MatrixLane, engine: str) -> None:
    """E2E4 AR-2/AR-3: the knowledge cell, unchanged, on the second graph backend."""
    await knowledge_cell(matrix_arango, engine, "AR")


@pytest.mark.parametrize("engine", ENGINES)
async def test_kn10_knowledge_tools_on_every_vector_store(matrix_vectors: MatrixLane, engine: str) -> None:
    """C3.25 (KN-10): the knowledge cell, unchanged, on each real vector backend: the same reads, the same refusals."""
    kind = matrix_vectors.services.vectors.get_service_name()
    await knowledge_cell(matrix_vectors, engine, f"KN10-{kind}")


async def knowledge_cell(matrix: MatrixLane, engine: str, cell: str) -> None:
    org = matrix.org
    ids = org.ids
    prompt = "\n".join([
        tool("knowledgegraph__search", query=QUERY),
        tool(FETCH, record_ids=[ids["rec-alice"]]),
        tool(FETCH, record_ids=[ids["rec-pub-full"]]),
        tool(FETCH, record_ids=[ids["rec-pub-answer"]]),
        tool(FETCH, record_ids=[ids["rec-trash"]]),
        tool(FETCH, record_ids=["rec-does-not-exist"]),
        tool("knowledgegraph__lookup_record", identifiers=[org.url("rec-bob")]),
        tool("knowledgegraph__lookup_record", identifiers=["https://drive.example/none/rec-does-not-exist"]),
        tool("knowledgegraph__navigate", node_id=ids["rec-bob"]),
        tool("knowledgegraph__navigate", node_id="rec-does-not-exist"),
        tool("knowledgegraph__lookup_record", identifiers=[ids["rec-alice"]]),
        tool("knowledgegraph__lookup_record", identifiers=[ids["rec-bob"]]),
        tool("knowledgegraph__lookup_record", identifiers=[ids["rec-trash"]]),
        tool("knowledgegraph__lookup_record", identifiers=["rec-does-not-exist"]),
        "hold",
        "say The south region closed at 800 units [s](ref2), invented [x](ref99).",
    ])
    result = await matrix.run(
        prompt, matrix_spec(org, kind=_engine(engine)), held=lambda run_id: checks(matrix, run_id)
    )
    assert result.status == "completed", (result.status, result.dispositions)
    out = result.tool_results()
    assert len(out) == 14, out
    search, alice, full, answer_only, trash, missing, lookup_bob, lookup_none, nav_bob, nav_none = out[:10]
    by_id_alice, by_id_bob, by_id_trash, by_id_none = out[10:]
    # KN-1: the user's readable records, never another user's or the trash.
    for marker in ("ALPHA7319", "GAMMA2207", "FOXTROT1162", "ECHO4473"):
        assert marker in search, (marker, search[:2000])
    for marker in ("BRAVO5581", "TANGO8840"):
        assert marker not in "\n".join(out), marker
    # KN-3: the whole record the run was shown, labelled refN.
    assert "ALPHA7319" in alice and "west region closed at 400" in alice and "[ref" in alice
    # KN-5: a full publication opens, an answer-only one does not.
    assert "FOXTROT1162" in full
    assert "ECHO4473" not in answer_only and "answers only" in answer_only
    # KN-2, KN-4: another user's record and the trash read exactly as a record that does not exist.
    assert same_answer(trash, ids["rec-trash"], missing, "rec-does-not-exist"), (trash, missing)
    assert same_answer(lookup_bob, org.url("rec-bob"), lookup_none, "https://drive.example/none/rec-does-not-exist")
    assert same_answer(nav_bob, ids["rec-bob"], nav_none, "rec-does-not-exist"), (nav_bob, nav_none)
    # KN-7 (E2E4): a Record ID resolves to the record it names, through the same access gate.
    assert ids["rec-alice"] in by_id_alice and "Not found" not in by_id_alice, by_id_alice
    for denied, rid in ((by_id_bob, ids["rec-bob"]), (by_id_trash, ids["rec-trash"])):
        assert same_answer(denied, rid, by_id_none, "rec-does-not-exist"), (denied, by_id_none)
    # KN-6: the cited ref resolves to the record's block; the invented one is neither recorded nor kept.
    recorded = {r["ref"]: r["url"] for c in result.of("x.pipeshub.citations.recorded") for r in c.get("refs") or []}
    assert f"/record/{ids['rec-alice']}/" in recorded.get("ref2", ""), recorded
    assert "ref99" not in recorded
    (answer,) = result.of("x.pipeshub.answer.finalized")
    assert "ref99" not in answer["answer"] and "invented" in answer["answer"], answer
    assert ids["rec-alice"] in json.dumps(answer["citations"]), answer["citations"]
    assert_confined_cell(matrix, result, f"{cell}/{engine}")


@pytest.mark.parametrize("engine", ENGINES)
async def test_mc_mcp_tools_through_a_runner(matrix: MatrixLane, engine: str) -> None:
    org = matrix.org
    grant = (mcp_tool("echo"),)
    text = f"hello from {engine} {uuid.uuid4().hex[:6]}"
    decided: list[str] = []

    def decide(interaction: str) -> str:
        # MC-5: the first call approved, the second denied.
        decided.append(interaction)
        return "approved" if len(decided) == 1 else "denied"

    # A write ends the turn until a person decides, so the sandbox is checked at the turn's first model call.
    prompt = "\n".join([
        "hold",
        tool(SEARCH_TOOLS, query="echo"),
        tool(ECHO, text=text),
        tool(ECHO, text="this call is denied"),
        "say done",
    ])
    result = await matrix.run(
        prompt, matrix_spec(org, kind=_engine(engine), tools=grant, mcp=True), decide=decide,
        held=lambda run_id: checks(matrix, run_id, unoffered=ENV_DIGEST),
    )
    assert result.status == "completed", (result.status, result.dispositions)
    # MC-1: no admin approves MCP tools here (D-MCP-5): the server's listing held nothing for approval, and only the
    # description scanner could hold a tool. D-LAZY-4: the engine's list carries no MCP tool, only the gateway's
    # search, which finds the granted tool.
    assert "mcp_tool_unapproved" not in matrix.first_load and "mcp_tool_changed" not in matrix.first_load
    offered = set(result.model_calls[0].tools)
    assert any(name.endswith((SEARCH_TOOLS, CALL_TOOL)) for name in offered), sorted(offered)
    assert not any(name.endswith((ECHO, ENV_DIGEST, WHERE)) for name in offered), sorted(offered)
    assert [r for r in result.tool_results() if ECHO in r], result.tool_results()
    # MC-3 and MC-5: approved, the call round-trips; denied, it is refused and never run.
    assert len(decided) == 2, decided
    out = result.tool_results()
    assert any(text in r for r in out), out
    assert not any("this call is denied" in r for r in out), out
    intents = [d for d in result.of("tool.intent") if str(d.get("toolId", "")).startswith("mcp@")]
    assert len(intents) == 1, intents
    resolved = result.of("interaction.resolved")
    assert sorted(str(d.get("decision")) for d in resolved) == ["approved", "denied"], resolved
    seen = assert_confined_cell(matrix, result, f"MC/{engine}")
    # MC-2: a tool the run was not granted, called anyway with the sandbox's own placeholder, is refused at the gateway.
    assert "not_granted" in seen.unoffered, seen.unoffered[:600]
    # MC-4: the server holds a placeholder, never its secret, and nothing the engine can see holds it (OWD-1).
    assert MCP_SECRET not in json.dumps(result.events)
    assert await asyncio.to_thread(runner_environments, matrix) == (True, False)


@pytest.mark.parametrize("engine", ENGINES)
async def test_mc6_a_call_of_echo_by_its_bare_name_with_no_search_is_its_first_use(
    matrix: MatrixLane, engine: str
) -> None:
    """DOCK-R26-FU-2 (R27REST-FU-2, D-LAZY-4): the engine calls the server's ``echo`` by the server's own name through
    ``call_tool``, with no ``search_tools`` before it (a name a model has from the run's sources or its task). The
    call is the server's first use: it is listed, the tool built and run under its own rules, and journaled once."""
    text = f"bare {engine} {uuid.uuid4().hex[:6]}"
    result = await matrix.run(
        f"{tool('echo', text=text)}\nsay done",
        matrix_spec(matrix.org, kind=_engine(engine), tools=(mcp_tool("echo"),), mcp=True), decide=approve_all,
    )
    assert result.status == "completed", (result.status, result.dispositions)
    called = [c.name for call in result.model_calls for m in call.messages if m.role == "assistant" for c in m.tool_calls]
    assert not any(name.endswith(SEARCH_TOOLS) for name in called), called
    assert any(text in r for r in result.tool_results()), result.tool_results()
    intents = [d for d in result.of("tool.intent") if str(d.get("toolId", "")).startswith("mcp@")]
    assert [d.get("toolId") for d in intents] == [mcp_tool("echo")], intents


def runner_environments(matrix: MatrixLane) -> tuple[bool, bool]:
    """MC-4 on the runner (OWD-1, WP-06): whether its server's environment holds a placeholder for the secret, and
    whether any process of it holds the secret itself (read inside its container)."""
    assert matrix.runner is not None
    client = matrix.lane.stack.client
    placeholder = secret = False
    for info in matrix.runner.runners():
        # The key is deterministic, so other lanes on this daemon hold runners under it: this runner's own id only.
        found = client.api.containers(filters={"label": [f"mcpRunnerId={info.runner_id}"]})
        for container in found:
            created = client.api.exec_create(
                container["Id"], ["/bin/sh", "-c", "cat /proc/[0-9]*/environ 2>/dev/null | tr '\\0' '\\n'"], user="1000:1000"
            )
            environments = client.api.exec_start(created["Id"]).decode("utf-8", "replace")
            placeholder = placeholder or f"{SECRET_NAME}=pxh-ph-" in environments
            secret = secret or MCP_SECRET in environments
    return placeholder, secret


@pytest.mark.parametrize("engine", ENGINES)
async def test_cn_a_connector_read_and_write(matrix: MatrixLane, engine: str) -> None:
    org = matrix.org
    jira = matrix.jira
    assert jira is not None
    summary = f"E2E3 {engine} {uuid.uuid4().hex[:6]}"
    prompt = "\n".join([
        "hold",
        tool("jira__search_issues", jql="project = OPS"),
        tool("jira__create_issue", project_key="OPS", summary=summary, issue_type_name="Task"),
        "say filed",
    ])
    before = len(jira.seen)
    result = await matrix.run(
        prompt, matrix_spec(org, kind=_engine(engine), tools=(JIRA_READ, JIRA_WRITE)), decide=approve_all,
        held=lambda run_id: checks(matrix, run_id),
    )
    assert result.status == "completed", (result.status, result.dispositions)
    out = result.tool_results()
    # CN-1: the read comes back from Jira with the toolset's credential.
    assert any("OPS-1" in r for r in out), out
    assert {a for _, _, a in jira.seen[before:]} == {f"Bearer {JIRA_TOKEN}"}
    # CN-2: the approved write reached Jira once, journaled once.
    assert jira.creates[summary] == 1, jira.creates
    writes = [d for d in result.of("tool.intent") if d.get("toolId") == JIRA_WRITE]
    assert len(writes) == 1, result.of("tool.intent")
    # CN-3: the credential never left the worker (sandbox scan above; journal; model requests).
    assert JIRA_TOKEN not in json.dumps(result.events)
    assert JIRA_TOKEN not in json.dumps([m.content for call in result.model_calls for m in call.messages])
    assert_confined_cell(matrix, result, f"CN/{engine}")


@pytest.mark.parametrize("engine", ENGINES)
async def test_bl_an_admins_block_refuses_the_tool_on_the_real_engine(matrix: MatrixLane, engine: str) -> None:
    """BL (SYNC4 §10, BUGS-A): main's per-tool Block rule reaches the engine path. With ``create_issue`` blocked
    in the org's tool rules, the engine's call is refused with main's wording before anything is asked or sent;
    the unblocked read in the same turn still reaches Jira (positive control)."""
    from app.agents.constants.toolset_constants import get_toolset_tool_rules_path
    from tests.unit.harness.engines.matrix.services import JIRA_INSTANCE

    jira = matrix.jira
    assert jira is not None
    summary = f"BL {engine} {uuid.uuid4().hex[:6]}"
    prompt = "\n".join([
        tool("jira__create_issue", project_key="OPS", summary=summary, issue_type_name="Task"),
        tool("jira__search_issues", jql="project = OPS"),
        "say refused",
    ])
    rules = get_toolset_tool_rules_path(JIRA_INSTANCE)
    matrix.services.config.put(rules, {"tools": {"create_issue": "block"}})
    try:
        # Admins' toolset rules are read where the edition turns organization rules on (main #4326).
        with patch.object(edition_config, "organization_toolset_tool_rules", True):
            result = await matrix.run(
                prompt, matrix_spec(matrix.org, kind=_engine(engine), tools=(JIRA_READ, JIRA_WRITE)), decide=approve_all
            )
    finally:
        matrix.services.config.put(rules, None)
    assert result.status == "completed", (result.status, result.dispositions)
    refused, read = result.tool_results()[:2]
    assert "blocked in the organization's tool approval rules" in refused, refused[:600]
    assert jira.creates[summary] == 0, jira.creates
    assert result.of("interaction.requested") == [], "a blocked tool is never asked for"
    assert [d for d in result.of("tool.intent") if d.get("toolId") == JIRA_WRITE] == []
    assert "OPS-1" in read, read[:600]


@pytest.mark.parametrize("engine", ENGINES)
async def test_cn4_a_write_is_journaled_once_across_a_crash_and_a_redrive(matrix: MatrixLane, engine: str) -> None:
    """CN-4: the worker process is ``SIGKILL``ed after the create reached Jira and before its outcome was journaled;
    the next worker settles the dangling intent ``unknown`` and never sends it again."""
    jira = matrix.jira
    assert jira is not None
    summary = f"E2E3 crash {engine} {uuid.uuid4().hex[:6]}"
    prompt = tool("jira__create_issue", project_key="OPS", summary=summary, issue_type_name="Task") + "\nsay filed"
    run_id = f"run-e2e3-{uuid.uuid4().hex[:8]}"
    matrix.lane.model.brain.holds.clear()
    await matrix.admit(run_id, prompt, matrix_spec(matrix.org, kind=_engine(engine), tools=(JIRA_WRITE,)), f"ses-{run_id}")
    held = await matrix.attempt(run_id)
    assert held["status"] in ("waiting_approval", "suspended"), held
    for interaction in open_interactions(await matrix.lane.events(run_id)):
        await matrix.lane.resolve(run_id, interaction, "approved")
    assert await matrix.killed_attempt(run_id, "worker.tool.after_effect") is None, "the worker outlived its kill"
    assert jira.creates[summary] == 1
    status = (await matrix.lane.until_claimable(run_id, timeout_s=KILLED_LEASE_MS / 1000 + 20))["status"]
    for _ in range(3):
        if status in ("completed", "failed", "quarantined", "cancelled"):
            break
        status = (await matrix.attempt(run_id))["status"]
    events = await matrix.lane.events(run_id)
    assert status == "completed", (status, [e["type"] for e in events])
    assert jira.creates[summary] == 1, jira.creates
    intents = [e for e in events if e["type"] == "tool.intent" and e["data"].get("toolId") == JIRA_WRITE]
    assert len(intents) == 1, intents
    unknown = [e for e in events if e["type"] == "tool.unknown"]
    assert [e["data"]["invocationId"] for e in unknown] == [intents[0]["data"]["invocationId"]], unknown


@pytest.mark.parametrize("engine", ENGINES)
async def test_rd_the_surfaced_set_survives_a_crash_and_a_redrive(matrix: MatrixLane, engine: str) -> None:
    """E2E4 RD-4: attempt 1 searches and files a held write; approved, attempt 2 is ``SIGKILL``ed after the write's
    effect; attempt 3, restored from attempt 1's checkpoint, fetches the record the search surfaced without
    searching again (the surfaced set is rebuilt from the journal's citation deltas)."""
    jira = matrix.jira
    assert jira is not None
    org = matrix.org
    summary = f"E2E4 redrive {engine} {uuid.uuid4().hex[:6]}"
    prompt = "\n".join([
        tool("knowledgegraph__search", query=QUERY),
        tool("jira__create_issue", project_key="OPS", summary=summary, issue_type_name="Task"),
        "after " + tool(FETCH, record_ids=[org.ids["rec-alice"]]),
        "say filed",
    ])
    run_id = f"run-e2e4-{uuid.uuid4().hex[:8]}"
    matrix.lane.model.brain.holds.clear()
    calls_before = len(matrix.lane.model.model_calls)
    await matrix.admit(run_id, prompt, matrix_spec(org, kind=_engine(engine), tools=(JIRA_WRITE,)), f"ses-{run_id}")
    held = await matrix.attempt(run_id)
    assert held["status"] in ("waiting_approval", "suspended"), held
    for interaction in open_interactions(await matrix.lane.events(run_id)):
        await matrix.lane.resolve(run_id, interaction, "approved")
    assert await matrix.killed_attempt(run_id, "worker.tool.after_effect") is None, "the worker outlived its kill"
    status = (await matrix.lane.until_claimable(run_id, timeout_s=KILLED_LEASE_MS / 1000 + 20))["status"]
    for _ in range(3):
        if status in ("completed", "failed", "quarantined", "cancelled"):
            break
        status = (await matrix.attempt(run_id))["status"]
    events = await matrix.lane.events(run_id)
    assert status == "completed", (status, [e["type"] for e in events])
    assert jira.creates[summary] == 1, jira.creates
    last = matrix.lane.model.model_calls[calls_before:][-1]
    searches = [c for m in last.messages if m.role == "assistant" for c in m.tool_calls if c.name.endswith("knowledgegraph__search")]
    assert len(searches) == 1, [c.name for m in last.messages for c in m.tool_calls]
    fetched = [m.content for m in last.messages if m.role == "tool"][-1]
    assert "ALPHA7319" in fetched and "record_not_shown" not in fetched, fetched[:1200]


SKILL_MD = "---\nname: {name}\ndescription: Use when deploying.\n---\n\nStep 1. {marker}.\n"


async def pinned_skill(matrix: MatrixLane, name: str, marker: str) -> tuple[str, str]:
    """A custom skill of alice's holding ``marker``, pinned as the run definition route pins an agent's skill."""
    from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
    from app.agents.agent_loop.skills.graph_store import GraphSkillStore
    from app.agents.run_definition import (
        AgentRunDefinition,
        RunDefinitionSkill,
        pin_skill_content,
    )
    from app.config.constants.arangodb import CollectionNames
    from tests.unit.harness.engines.matrix.services import AGENT

    org, graph = matrix.org, matrix.services.graph
    _ = await GraphSkillStore(graph, org.org_id, org.user_id("alice")).create_skill(
        name, SKILL_MD.format(name=name, marker=marker)
    )
    # Assigned to the matrix agent, as the agent route links it: the assignment is what keeps it in force for the run.
    agents, skills = CollectionNames.AGENT_INSTANCES.value, CollectionNames.AGENT_SKILLS.value
    agent = {
        "_key": AGENT, "id": AGENT, "name": "Matrix agent", "description": "The tool matrix's agent.",
        "startMessage": "Hello.", "systemPrompt": "You help.", "models": [], "createdBy": org.user_id("alice"),
        "createdAtTimestamp": 1,
    }
    _ = await graph.batch_upsert_nodes([agent], agents)
    _ = await graph.batch_create_edges(
        [{"_from": f"{agents}/{AGENT}", "_to": f"{skills}/{org.org_id}_{name}", "skillName": name, "createdAtTimestamp": 1}],
        CollectionNames.AGENT_HAS_SKILL.value,
    )
    definition = AgentRunDefinition(agentKey="a", definitionVersion=1, skills=(RunDefinitionSkill(name=name),))
    (pinned,) = (await pin_skill_content(definition, GraphSkillSource(graph), org.org_id)).skills
    assert pinned.bundle_digest is not None
    return name, pinned.bundle_digest


@pytest.mark.parametrize("engine", ENGINES)
async def test_sk_a_pinned_skill_is_in_the_sandbox_as_admitted_and_no_other(matrix: MatrixLane, engine: str) -> None:
    """QA-3 SK-12 (SKL-10): the engine's own shell reads the skills the controller seeded. The run pinned one of
    alice's two skills, and its author edited it after admission."""
    from app.agents.agent_loop.skills.graph_store import GraphSkillStore

    org = matrix.org
    suffix = f"{engine.replace('_', '-')}-{uuid.uuid4().hex[:6]}"
    name, digest = await pinned_skill(matrix, f"deploy-{suffix}", "ADMITTED4417")
    unpinned, _ = await pinned_skill(matrix, f"audit-{suffix}", "UNPINNED9026")
    store = GraphSkillStore(matrix.services.graph, org.org_id, org.user_id("alice"))
    _ = await store.update_skill(name, SKILL_MD.format(name=name, marker="EDITED7733"))
    prompt = "\n".join([
        "shell ls /skills",
        f"shell cat /skills/{name}/SKILL.md",
        "hold",
        "say read",
    ])
    result = await matrix.run(
        prompt, matrix_spec(org, kind=_engine(engine), skills=((name, digest),)),
        held=lambda run_id: checks(matrix, run_id),
    )
    assert result.status == "completed", (result.status, result.dispositions)
    listed, read = result.tool_results()[:2]
    # SK-12: exactly the pinned skill, with the content admitted.
    assert name in listed and unpinned not in listed, listed
    assert "ADMITTED4417" in read and "EDITED7733" not in read, read
    assert "UNPINNED9026" not in "\n".join(result.tool_results())
    assert_confined_cell(matrix, result, f"SK/{engine}")


@pytest.mark.parametrize("engine", ENGINES)
async def test_sb_knowledge_then_code_then_an_artifact(matrix: MatrixLane, engine: str) -> None:
    org = matrix.org
    compute = (
        "mkdir -p out && printf 'region,units\\nnorth,1200\\nsouth,800\\nwest,400\\n' "
        "| awk -F, 'NR>1{s+=$2} END{print \"total,\" s}' > out/summary.csv && cat out/summary.csv"
    )
    prompt = "\n".join([
        tool("knowledgegraph__search", query=QUERY),
        tool(FETCH, record_ids=[org.ids["rec-alice"]]),
        f"shell {compute}",
        "hold",
        "say The regions total 2400 units [t](ref1); the table is in summary.csv.",
    ])
    result = await matrix.run(
        prompt, matrix_spec(org, kind=_engine(engine)), held=lambda run_id: checks(matrix, run_id)
    )
    assert result.status == "completed", (result.status, result.dispositions)
    # SB-1: the computation's file is published.
    assert len(result.of("x.pipeshub.artifact.published")) == 1, result.of("x.pipeshub.artifact.published")
    names = {str(p.get("name") or p.get("fileName")) for p in result.of("x.pipeshub.artifact.presented")}
    assert "summary.csv" in names, result.of("x.pipeshub.artifact.presented")
    assert matrix.files is not None
    assert any(b"total,2400" in v[-1] for v in matrix.files.documents.values()), "the published bytes"
    assert any("total,2400" in r for r in result.tool_results())
    # SB-2: the answer's label names the record it drew on.
    (answer,) = result.of("x.pipeshub.answer.finalized")
    label = answer.get("label") or {}
    assert label.get("complete") is True, label
    sources: list[dict[str, Any]] = list(label.get("sources") or [])
    assert org.ids["rec-alice"] in {s.get("recordId") for s in sources}, label
    # SB-3: a shared view shows the turn to a viewer who can read every source, a published one through its
    # publication (Node asks /records/access, which decides with this authorizer on the real graph), and hides
    # it from one who cannot.
    assert await shown_to(matrix, "alice", sources) and not await shown_to(matrix, "bob", sources)
    assert_confined_cell(matrix, result, f"SB/{engine}")


async def shown_to(matrix: MatrixLane, viewer: str, sources: list[dict[str, Any]]) -> bool:
    from app.harness.contracts.generated.pipeshub.citation_source import (
        PipeshubRecordSource,
    )
    from app.harness.knowledge.service import KnowledgePublications
    from app.services.artifact_registry.models import Actor
    from app.services.record_content import TieredRecordAuthorizer

    graph = matrix.services.graph
    actor = Actor(org_id=matrix.org.org_id, user_id=matrix.org.user_id(viewer))
    known = {f.alias or name for name, f in PipeshubRecordSource.model_fields.items()}
    asked = [PipeshubRecordSource.model_validate({k: v for k, v in s.items() if k in known}) for s in sources]
    statuses = await TieredRecordAuthorizer(graph).decide(
        actor, asked, publications=KnowledgePublications(graph, matrix.services.config)
    )
    return bool(statuses) and all(status == "readable" for status in statuses)
