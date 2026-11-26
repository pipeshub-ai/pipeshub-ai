"""QA-27 QA27-1, QA27-4, QA27-7 (KNW-15, KNW-18, IX-LEG-KNW-3) and KN2-8, KN2-10: the knowledge read gate matrix over
the shared record-access fake.

Each cell of ``tests/support/knowledge_read_matrix.py`` is seeded into ``InMemoryRecordAccess`` (``read_world.py``)
with the real ``KnowledgePublications`` over an in-memory directory. For every cell and path, alice's run is the
production state (``build_initial_state``) and toolsets (``ToolsetSource`` over ``PipesHubToolLoader``, granted as
``build_catalog`` grants them), its tools called as the path dispatches them. The surfaces here are those whose decision is the record-access surface or the lookup gate; the
integration level adds search, navigation and listing on a real graph.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Final

import pytest
import pytest_asyncio

from app.config.constants.service import config_node_constants
from app.services.featureflag.config.config import CONFIG
from tests.support.config_service import FakeConfigurationService
from tests.support.knowledge_publications import ORG
from tests.support.knowledge_read_matrix import (
    BY_IDENTIFIER,
    CELLS,
    OWNERSHIPS,
    PATHS,
    PUBLICATIONS,
    Cell,
    PathName,
    Reader,
    Surface,
    access,
    assert_one_refusal,
    expected,
    expected_on,
    normalized,
    outcome,
    run_tools,
)
from tests.unit.harness.engines.matrix.services import StoredFiles, blob_seam
from tests.unit.harness.knowledge.read_world import publications, seeded_world

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic import JsonValue

    from app.harness.knowledge.service import KnowledgePublications
    from tests.support.knowledge_read_world import Seeded, World
    from tests.unit.harness.knowledge.read_world import KnowledgeReads

FETCH: Final = "knowledgegraph__fetch_record"
LOOKUP: Final = "knowledgegraph__lookup_record"
SLACK_THREAD: Final = "slack__fetch_slack_thread"
SLACK_NEARBY: Final = "slack__fetch_slack_nearby_messages"
SURFACES: Final[tuple[Surface, ...]] = (
    "fetch", "fetch_surfaced", "lookup_id", "lookup_external", "lookup_url", "slack_thread", "slack_nearby",
)


@dataclass
class Observed:
    world: World
    answers: dict[tuple[Surface, PathName, Cell], str] = field(default_factory=dict[tuple[Surface, PathName, Cell], str])
    views: dict[Reader, dict[Cell, str]] = field(default_factory=dict[Reader, dict[Cell, str]])


def _config() -> FakeConfigurationService:
    return FakeConfigurationService(
        {config_node_constants.AI_MODELS.value: {"embedding": []}},
        flags={CONFIG.ENABLE_KNOWLEDGE_PUBLICATIONS: True, CONFIG.ENABLE_SOFT_DELETE: True},
    )


def _context(world: World, graph: KnowledgeReads, config: FakeConfigurationService) -> Any:  # noqa: ANN401
    """Alice's request state as the agent route and the worker build it (``build_initial_state``)."""
    from app.agents.agent_loop.context import AgentContext
    from app.modules.agents.qna.chat_state import build_initial_state
    from tests.unit.agents.adapter.conftest import FakeChatModel

    query_info: dict[str, Any] = {
        "query": "ledger note",
        "messages": [],
        "previous_conversations": [],
        "chatMode": "quick",
        "filters": {"apps": [world.drive, world.slack], "kb": []},
        "knowledge": [{"connectorId": world.drive, "type": "Drive"}, {"connectorId": world.slack, "type": "SLACK"}],
        "toolsets": [],
        "mcpServers": [],
        "conversationId": "ses-qa27",
    }
    alice = world.user_id("alice")
    user_info = {"userId": alice, "orgId": ORG, "userEmail": f"{alice}@corp.example", "_key": world.user_key("alice")}
    state = build_initial_state(
        query_info, user_info, FakeChatModel(), logging.getLogger("tests.qa27"), None, graph, None, config,
        "gpt-x", "model-1", {"orgId": ORG, "accountType": "enterprise", "name": "QA-27 Org"}, "react",
        has_sql_connector=False, has_slack_connector=True,
    )
    return AgentContext.from_chat_state(state, protocol="agui", llm_provider="openai", run_id="run-qa27")


async def _tools(world: World, graph: KnowledgeReads, config: FakeConfigurationService, path: PathName) -> Any:  # noqa: ANN401
    """Alice's run on ``path``: the tools ``ToolsetSource`` sources and ``build_catalog`` grants a run whose pin names
    no connector tool (the knowledge agent's)."""
    from app.harness.assembly.assembler import build_catalog
    from app.harness.assembly.tool_sources import ToolsetSource
    from harness_core.tools.registry import ToolRegistry

    context = _context(world, graph, config)
    run = SimpleNamespace(org_id=ORG, session_id="ses-qa27", run_id="run-qa27", tool_ids=(), mcp_instances=(), mcp_at_first_use=())
    env = SimpleNamespace(context=context, web_search_provider=None, granted_toolsets=(), unavailable=())
    registry = ToolRegistry()
    sourced = await ToolsetSource().load(env, run, registry)  # type: ignore[arg-type]
    effects = build_catalog(run, registry, sourced).effects()  # type: ignore[arg-type]
    return context, await run_tools(path, context, registry, effects)


def _calls(drive: Seeded, slack: Seeded) -> tuple[tuple[Surface, str, dict[str, JsonValue]], ...]:
    return (
        ("fetch", FETCH, {"record_ids": [drive.id]}),
        ("lookup_id", LOOKUP, {"identifiers": [drive.id]}),
        ("lookup_external", LOOKUP, {"identifiers": [drive.external_id]}),
        ("lookup_url", LOOKUP, {"identifiers": [drive.url]}),
        ("slack_thread", SLACK_THREAD, {"record_id": slack.id}),
        ("slack_nearby", SLACK_NEARBY, _nearby(slack)),
    )


def _nearby(slack: Seeded) -> dict[str, JsonValue]:
    return {"record_id": slack.id, "timestamp": "2026-10-09T12:00:00Z", "direction": "before"}


async def _views(world: World, graph: KnowledgeReads, service: KnowledgePublications, reader: Reader) -> dict[Cell, str]:
    from app.harness.contracts.generated.pipeshub.citation_source import (
        PipeshubRecordSource,
    )
    from app.services.artifact_registry.models import Actor
    from app.services.record_content import TieredRecordAuthorizer

    sources = [
        PipeshubRecordSource(kind="indexed", record_id=world.record(c, "drive").id, publication_id=world.publication(c, "drive"))
        for c in CELLS
    ]
    actor = Actor(org_id=ORG, user_id=world.user_id(reader))
    statuses = await TieredRecordAuthorizer(graph).decide(actor, sources, publications=service)
    return dict(zip(CELLS, statuses, strict=True))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def observed() -> AsyncIterator[Observed]:
    config = _config()
    world, graph = seeded_world(config)
    service = await publications(world, graph)
    seen = Observed(world)
    with pytest.MonkeyPatch.context() as patch, blob_seam(world, StoredFiles()):  # type: ignore[arg-type]
        # The gate builds its own publications from the graph; here they are the in-memory directory's.
        patch.setattr("app.harness.knowledge.read_gate.KnowledgePublications", lambda *_, **__: service)
        for path in PATHS:
            for cell in CELLS:
                drive, slack = world.record(cell, "drive"), world.record(cell, "slack")
                context, tools = await _tools(world, graph, config, path)
                for surface, name, arguments in _calls(drive, slack):
                    seen.answers[(surface, path, cell)] = await tools.call(name, arguments)
                _surfaced(context, drive.id)
                seen.answers[("fetch_surfaced", path, cell)] = await tools.call(FETCH, {"record_ids": [drive.id]})
        for reader in ("alice", "carol"):
            seen.views[reader] = await _views(world, graph, service, reader)
    yield seen


def _surfaced(context: Any, record_id: str) -> None:  # noqa: ANN401
    """The record shown to the run as a knowledge call shows it (search, navigation and lookup record what they
    returned the same way)."""
    from app.modules.agents.qna.chat_state import remember_record_ids

    remember_record_ids(context.tool_state, [record_id])


def _cases() -> list[tuple[Surface, PathName]]:
    return [(s, p) for s in SURFACES for p in PATHS]


def _seeded(observed: Observed, surface: Surface, cell: Cell) -> Seeded:
    return observed.world.record(cell, "slack" if surface.startswith("slack") else "drive")


@pytest.mark.parametrize(("surface", "path"), _cases())
def test_qa27_1_every_cell_shows_what_the_matrix_says(observed: Observed, surface: Surface, path: PathName) -> None:
    wrong: dict[str, tuple[str, str, str]] = {}
    for cell in CELLS:
        text, seeded = observed.answers[(surface, path, cell)], _seeded(observed, surface, cell)
        got = outcome(surface, text, content=seeded.content, name=seeded.name)
        if got != (want := expected_on(surface, path, cell)):
            wrong[cell.key] = (want, got, text[:240])
    assert wrong == {}, wrong


@pytest.mark.parametrize(("surface", "path"), [(s, p) for s, p in _cases() if s in BY_IDENTIFIER])
def test_qa27_1_a_hidden_record_reads_like_one_that_does_not_exist(
    observed: Observed, surface: Surface, path: PathName
) -> None:
    hidden = {
        cell: normalized(observed.answers[(surface, path, cell)], _seeded(observed, surface, cell).names())
        for cell in CELLS
        if expected_on(surface, path, cell) == "hidden"
    }
    assert Cell("missing", "none") in hidden and Cell("trashed", "full") in hidden
    assert_one_refusal(surface, path, hidden)


@pytest.mark.parametrize("reader", ["alice", "carol"])
def test_qa27_4_the_shared_view_shows_each_viewer_what_that_viewer_may_read(observed: Observed, reader: Reader) -> None:
    views = observed.views[reader]
    wrong = {c.key: views[c] for c in CELLS if (expected("projection", c, reader) == "served") != (views[c] == "readable")}
    assert wrong == {}, wrong
    assert_one_refusal("projection", reader, {c: s for c, s in views.items() if s != "readable"})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def nearby_live() -> AsyncIterator[tuple[World, dict[tuple[PathName, Cell], list[str]]]]:
    """``fetch_slack_nearby_messages`` on every cell and path, alice holding the channel too, so the record-access gate
    on the anchor is the only thing between a call and the live Slack API: (path, cell) -> the connector instances a
    live Slack client was built for."""
    config = _config()
    world, graph = seeded_world(config, channel_readers=frozenset({"alice"}))
    service = await publications(world, graph)
    built: list[str] = []
    clients: dict[tuple[PathName, Cell], list[str]] = {}

    async def live_slack(*_: object, connector_instance_id: str, **__: object) -> None:
        built.append(connector_instance_id)
        raise ConnectionError("no live Slack in this test")

    with pytest.MonkeyPatch.context() as patch, blob_seam(world, StoredFiles()):  # type: ignore[arg-type]
        patch.setattr("app.harness.knowledge.read_gate.KnowledgePublications", lambda *_, **__: service)
        patch.setattr("app.utils.fetch_slack_nearby_messages.SlackClient.build_from_services", live_slack)
        for path in PATHS:
            for cell in CELLS:
                _, tools = await _tools(world, graph, config, path)
                slack = world.record(cell, "slack")
                before = len(built)
                await tools.call(SLACK_NEARBY, _nearby(slack))
                clients[(path, cell)] = built[before:]
    yield world, clients


@pytest.mark.parametrize("path", PATHS)
def test_kn2_10_slack_nearby_reaches_slack_only_for_the_readers_own_record(
    nearby_live: tuple[World, dict[tuple[PathName, Cell], list[str]]], path: PathName
) -> None:
    """Live Slack is reached after the gate admitted the anchor as the reader's own record, on that record's
    connector; a published, revoked, expired, trashed, missing or another user's record never reaches it."""
    world, clients = nearby_live
    built = {cell.key: clients[(path, cell)] for cell in CELLS}
    want = {cell.key: [world.slack] if access(cell) == "own" else [] for cell in CELLS}
    assert built == want


def test_qa27_7_the_matrix_is_every_ownership_by_every_publication() -> None:
    assert len(CELLS) == len(set(CELLS)) == (len(OWNERSHIPS) - 1) * len(PUBLICATIONS) + 1
    assert {c.ownership for c in CELLS} == set(OWNERSHIPS)
    assert {c.publication for c in CELLS} == set(PUBLICATIONS)


@pytest.mark.parametrize(
    ("cell", "alice", "carol"),
    [
        (Cell("own", "none"), "own", "none"),
        (Cell("group", "other_audience"), "own", "published_full"),
        (Cell("org", "answer_only"), "own", "own"),
        (Cell("other", "full"), "published_full", "none"),
        (Cell("other", "answer_only"), "published_answer_only", "none"),
        (Cell("other", "revoked"), "none", "none"),
        (Cell("other", "expired"), "none", "none"),
        (Cell("other", "other_audience"), "none", "published_full"),
        (Cell("trashed", "full"), "none", "none"),
        (Cell("trashed", "other_audience"), "none", "none"),
        (Cell("missing", "none"), "none", "none"),
    ],
)
def test_qa27_7_the_access_rule_is_the_documented_one(cell: Cell, alice: str, carol: str) -> None:
    assert (access(cell, "alice"), access(cell, "carol")) == (alice, carol)
