"""QA-27 QA27-2..QA27-5 (KNW-17, KNW-18, IX-G17, IX-LEG-KNW-1..3): the knowledge read gate matrix on a real Neo4j and
a real ArangoDB.

Each cell of ``tests/support/knowledge_read_matrix.py`` is seeded as a Drive file and a Slack thread reply
(``knowledge_read_world.py``). For every cell and path, alice's run is built as a worker builds it
(``build_tool_environment``, ``ToolsetSource``, ``build_catalog``) over the production provider, and calls each
knowledge tool as that path dispatches it. Replaced, each for a stated reason: the agent definition read (a dict, not
the agent service's graph shape), blob storage (record reads answer from the seeded blocks), the vector store (the
contract fake with a hash embedder), the configuration store (declared documents). Every access rule runs on the
graph.

Requires the servers of ``tests/integration/compose/graph-db.yml`` (or any Neo4j 5 and ArangoDB 3.12 at
``NEO4J_TEST_URI`` / ``ARANGO_TEST_URL``); skipped when they are absent.
Run: pytest tests/integration/graph_db/test_knowledge_read_matrix.py -m integration
"""

from __future__ import annotations

import dataclasses
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

import pytest
import pytest_asyncio

from app.config.constants.service import config_node_constants
from app.core import org_context
from app.services.featureflag.config.config import CONFIG
from tests.integration.graph_db.knowledge_read_seed import seed
from tests.integration.graph_db.test_knowledge_publications import (
    arango_provider,  # noqa: F401  # pyright: ignore[reportUnusedImport]
    neo4j_provider,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from tests.support.config_service import FakeConfigurationService
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.knowledge_read_matrix import (
    BY_IDENTIFIER,
    CELLS,
    PATHS,
    Cell,
    Outcome,
    PathName,
    Reader,
    Surface,
    assert_one_refusal,
    expected,
    expected_on,
    normalized,
    outcome,
    run_tools,
    without_closest_id,
)
from tests.unit.harness.engines.matrix.services import (
    AGENT,
    MatrixServices,
    StoredFiles,
    blob_seam,
    stub_agent_reads,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pydantic import JsonValue

    from app.harness.assembly.tool_environment import ToolEnvironment
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from tests.support.knowledge_read_matrix import RunTools
    from tests.support.knowledge_read_world import Kind, Seeded, World

pytestmark = pytest.mark.integration

TOOL_SURFACES: Final[tuple[Surface, ...]] = (
    "fetch", "search", "fetch_surfaced", "lookup_id", "lookup_external", "lookup_url", "navigate", "list",
    "slack_thread", "slack_nearby",
)
FETCH: Final = "knowledgegraph__fetch_record"
SLACK_THREAD: Final = "slack__fetch_slack_thread"
SLACK_NEARBY: Final = "slack__fetch_slack_nearby_messages"
# "ledger" and "note" are in every record's block: a query that ranks every record a reader may see.
EVERYTHING: Final = "ledger note"


@dataclass
class Observed:
    world: World
    # (surface, path, cell) -> the text the model (or engine) read
    answers: dict[tuple[Surface, PathName, Cell], str] = field(default_factory=dict[tuple[Surface, PathName, Cell], str])
    # reader -> cell -> the shared view's status of alice's citation of that cell's Drive record
    views: dict[Reader, dict[Cell, str]] = field(default_factory=dict[Reader, dict[Cell, str]])
    # reader -> the markers a chat run's prefetch of that reader returned
    prefetched: dict[Reader, set[str]] = field(default_factory=dict[Reader, set[str]])
    # the first distinct errors logged while observing, tracebacks included: a tool answers a failure with a
    # one-line message, and the module fixture's captured log is shown with no test's failure
    errors: list[str] = field(default_factory=list[str])

    def why(self) -> str:
        return "\n\n".join(self.errors) or "(nothing logged at ERROR while observing)"


class _Errors(logging.Handler):
    LIMIT: Final = 5

    def __init__(self, into: list[str]) -> None:
        super().__init__(logging.ERROR)
        self.setFormatter(logging.Formatter("%(name)s: %(message)s"))
        self._into = into
        self._seen: set[str] = set()

    def emit(self, record: logging.LogRecord) -> None:
        if len(self._into) < self.LIMIT and (key := f"{record.name}:{record.getMessage()}") not in self._seen:
            self._seen.add(key)
            self._into.append(self.format(record))


def _config(world: World) -> FakeConfigurationService:
    return FakeConfigurationService(
        {
            "/services/agentHarness/approvals": None,
            config_node_constants.AI_MODELS.value: {"embedding": []},
            # The browse signs its page cursors with a key derived from this (kh_cursor), as every install has one.
            config_node_constants.SECRET_KEYS.value: {"scopedJwtSecret": "knowledge-read-matrix-cursor-secret"},
        },
        flags={CONFIG.ENABLE_KNOWLEDGE_PUBLICATIONS: True, CONFIG.ENABLE_SOFT_DELETE: True},
    )


def _agent(world: World) -> dict[str, Any]:
    return {
        "_key": AGENT, "name": "Knowledge agent", "systemPrompt": "You answer from the org's knowledge.",
        "instructions": "", "isServiceAccount": False, "sendUserContext": False, "toolsets": [], "mcpServers": [],
        "knowledge": [{"connectorId": world.drive, "type": "Drive"}, {"connectorId": world.slack, "type": "SLACK"}],
        "models": [],
    }


def _as_run(world: World, reader: Reader, *, chat: bool = False) -> Any:  # noqa: ANN401 - a context manager
    """The org binding a worker gives the run's task (``PipesHubRuntimeAssembler.build``), which the knowledge tools
    pin their principal to."""
    from app.harness.assembly.assembler import run_org_context

    return org_context.bind(run_org_context(_run(world, reader, chat=chat)))


def _run(world: World, reader: Reader, *, chat: bool = False) -> Any:  # noqa: ANN401 - RunContext
    from app.harness.assembly.context import RunContext
    from tests.unit.harness.assembly import world as w

    spec = w.pinned_spec(
        tool_ids=[], apps=[] if chat else [world.drive, world.slack], kb=[], strict_scope=False, agent=not chat,
        chat={"mode": "internal_search", "internalSearch": True} if chat else None,
    )
    if not chat:
        spec.extensions["x.pipeshub"]["agent"]["agentKey"] = AGENT  # type: ignore[index]
    claim = w.claim(spec, query=EVERYTHING, chat_mode="internal_search" if chat else "quick")
    run = claim.run.model_copy(update={"org_id": world.org_id, "actor_id": world.user_id(reader), "session_id": "ses-qa27"})
    return RunContext.from_claim(dataclasses.replace(claim, org_id=world.org_id, session_id="ses-qa27", run=run))


async def _environment(services: MatrixServices, world: World, reader: Reader, *, chat: bool = False) -> tuple[Any, ToolEnvironment]:
    from app.harness.assembly.tool_environment import build_tool_environment
    from tests.unit.harness.engines.matrix.assembly import scripted_model

    run = _run(world, reader, chat=chat)
    return run, await build_tool_environment(run, services, await scripted_model(run, services))


async def _tools(services: MatrixServices, world: World, path: PathName) -> RunTools:
    """Alice's run on ``path``, its catalog as a worker builds it."""
    from app.harness.assembly.assembler import build_catalog
    from app.harness.assembly.tool_sources import ToolsetSource
    from harness_core.tools.registry import ToolRegistry

    run, env = await _environment(services, world, "alice")
    registry = ToolRegistry()
    sourced = await ToolsetSource().load(env, run, registry)
    return await run_tools(path, env.context, registry, build_catalog(run, registry, sourced).effects())


def _calls(drive: Seeded, slack: Seeded) -> tuple[tuple[Surface | None, str, dict[str, JsonValue]], ...]:
    """What one run asks about one cell, in order: a full read before anything surfaced the record, a search for
    it, a search every cell makes alike (not observed), the read again, each identifier kind, navigation, a listing
    by name, and the Slack record's thread and channel.

    A read of an id no readable record has lists the records shown latest, in the order their search ranked them
    (id recovery), and a search for the cell's own marker ranks the other records by that marker. The common search
    just before the read makes that list the same for every cell, so a hidden cell's read is compared with the
    missing cell's after the same conversation."""
    return (
        ("fetch", FETCH, {"record_ids": [drive.id]}),
        ("search", "knowledgegraph__search", {"query": drive.marker}),
        (None, "knowledgegraph__search", {"query": EVERYTHING}),
        ("fetch_surfaced", FETCH, {"record_ids": [drive.id]}),
        ("lookup_id", "knowledgegraph__lookup_record", {"identifiers": [drive.id]}),
        ("lookup_external", "knowledgegraph__lookup_record", {"identifiers": [drive.external_id]}),
        ("lookup_url", "knowledgegraph__lookup_record", {"identifiers": [drive.url]}),
        ("navigate", "knowledgegraph__navigate", {"node_id": drive.id}),
        ("list", "knowledgegraph__list_files", {"query": drive.marker}),
        ("slack_thread", SLACK_THREAD, {"record_id": slack.id}),
        ("slack_nearby", SLACK_NEARBY, {"record_id": slack.id, "timestamp": "2026-10-09T12:00:00Z", "direction": "before"}),
    )


async def _views(graph: IGraphDBProvider, world: World, config: FakeConfigurationService, reader: Reader) -> dict[Cell, str]:
    """``/records/access`` for ``reader`` (the authorizer and publications it composes) over alice's citation of
    each cell's Drive record, naming the publication it came through as a published citation does."""
    from app.harness.contracts.generated.pipeshub.citation_source import (
        PipeshubRecordSource,
    )
    from app.harness.knowledge.service import KnowledgePublications
    from app.services.artifact_registry.models import Actor
    from app.services.record_content import TieredRecordAuthorizer

    sources = [
        PipeshubRecordSource(
            kind="indexed", record_id=world.record(cell, "drive").id, publication_id=world.publication(cell, "drive")
        )
        for cell in CELLS
    ]
    statuses = await TieredRecordAuthorizer(graph).decide(
        Actor(org_id=world.org_id, user_id=world.user_id(reader)), sources,
        publications=KnowledgePublications(graph, config),
    )
    return dict(zip(CELLS, statuses, strict=True))


async def _prefetched(services: MatrixServices, world: World, reader: Reader) -> set[str]:
    """The markers a chat run of ``reader`` in the shared session prefetched (its goal step's upfront search)."""
    from app.agents.chat_modes.setup import start_prefetch

    _, env = await _environment(services, world, reader, chat=True)
    turn = env.chat
    assert turn is not None
    started = start_prefetch(
        turn.setup, env.context, turn.policy, retrieval_service=turn.retrieval_service,
        graph_provider=turn.graph_provider, is_multimodal_llm=False, log=logging.getLogger("tests.qa27.prefetch"),
    )
    assert started is not None
    result = await started
    text = result.formatted_context if result is not None else ""
    return {s.marker for s in world.records.values() if s.marker in text}


@pytest.fixture(scope="module", params=["neo4j", "arango"])
def backend(request: pytest.FixtureRequest) -> IGraphDBProvider:
    return request.getfixturevalue(f"{request.param}_provider")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def observed(backend: IGraphDBProvider) -> AsyncIterator[Observed]:
    graph = backend
    vectors = InMemoryVectorDBService()
    suffix = uuid.uuid4().hex[:6]
    world = await seed(graph, vectors, f"org-qa27-{suffix}", suffix)
    config = _config(world)
    graph.config_service = config  # type: ignore[attr-defined]
    stub_agent_reads(graph, _agent(world))
    services = MatrixServices(world, graph, config, _agent(world), vectors)  # type: ignore[arg-type]
    seen = Observed(world)
    errors = _Errors(seen.errors)
    logging.getLogger().addHandler(errors)
    try:
        with blob_seam(world, StoredFiles()):  # type: ignore[arg-type]
            with _as_run(world, "alice"):
                for path in PATHS:
                    for cell in CELLS:
                        tools = await _tools(services, world, path)
                        for surface, name, arguments in _calls(world.record(cell, "drive"), world.record(cell, "slack")):
                            answer = await tools.call(name, arguments)
                            if surface is not None:
                                seen.answers[(surface, path, cell)] = answer
            for reader in ("alice", "carol"):
                seen.views[reader] = await _views(graph, world, config, reader)
                with _as_run(world, reader, chat=True):
                    seen.prefetched[reader] = await _prefetched(services, world, reader)
    finally:
        logging.getLogger().removeHandler(errors)
    yield seen


def _kind(surface: Surface) -> Kind:
    return "slack" if surface.startswith("slack") else "drive"


def _outcome(observed: Observed, surface: Surface, path: PathName, cell: Cell) -> Outcome:
    seeded = observed.world.record(cell, _kind(surface))
    return outcome(surface, observed.answers[(surface, path, cell)], content=seeded.content, name=seeded.name)


def _cases(surfaces: tuple[Surface, ...]) -> list[Any]:
    return [pytest.param(surface, path) for surface in surfaces for path in PATHS]


@pytest.mark.parametrize(("surface", "path"), _cases(TOOL_SURFACES))
def test_qa27_2_every_cell_shows_what_the_matrix_says(observed: Observed, surface: Surface, path: PathName) -> None:
    wrong = {
        cell.key: (want, got, observed.answers[(surface, path, cell)][:240])
        for cell in CELLS
        if (want := expected_on(surface, path, cell))
        != (got := _outcome(observed, surface, path, cell))
    }
    assert wrong == {}, f"{wrong}\n\nlogged while observing:\n{observed.why()}"


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("surface", [s for s in TOOL_SURFACES if s in BY_IDENTIFIER])
def test_qa27_2_a_hidden_record_reads_like_one_that_does_not_exist(observed: Observed, surface: Surface, path: PathName) -> None:
    hidden = {
        cell: without_closest_id(
            normalized(observed.answers[(surface, path, cell)], observed.world.record(cell, _kind(surface)).names())
        )
        for cell in CELLS
        if expected_on(surface, path, cell) == "hidden"
    }
    assert Cell("missing", "none") in hidden
    assert_one_refusal(surface, path, hidden)


@pytest.mark.parametrize("surface", TOOL_SURFACES)
def test_qa27_3_the_loop_and_the_durable_kernel_answer_alike(observed: Observed, surface: Surface) -> None:
    differ = {
        cell.key
        for cell in CELLS
        if normalized(observed.answers[(surface, "loop", cell)], ())
        != normalized(observed.answers[(surface, "durable", cell)], ())
    }
    assert differ == set(), f"{differ}\n\nlogged while observing:\n{observed.why()}"


def test_qa27_4_the_shared_view_shows_the_reader_what_the_reader_may_read(observed: Observed) -> None:
    wrong = {
        cell.key: (want, got)
        for cell in CELLS
        if (want := expected("projection", cell)) != ("served" if (got := observed.views["alice"][cell]) == "readable" else "hidden")
    }
    assert wrong == {}, wrong


def test_qa27_4_another_viewer_sees_only_what_that_viewer_may_read(observed: Observed) -> None:
    wrong = {
        cell.key: got
        for cell in CELLS
        if (expected("projection", cell, "carol") == "served") != ((got := observed.views["carol"][cell]) == "readable")
    }
    assert wrong == {}, wrong


@pytest.mark.parametrize("reader", ["alice", "carol"])
def test_qa27_4_a_hidden_citation_reads_like_a_missing_one(observed: Observed, reader: Reader) -> None:
    hidden = {cell: status for cell, status in observed.views[reader].items() if status != "readable"}
    assert Cell("missing", "none") in hidden
    assert_one_refusal("projection", reader, hidden)


@pytest.mark.parametrize("reader", ["alice", "carol"])
def test_qa27_5_a_participants_prefetch_is_the_participants_own(observed: Observed, reader: Reader) -> None:
    world = observed.world
    readable = {
        s.marker
        for (cell, _), s in world.records.items()
        if expected("search", cell, reader) == "served"
    }
    assert observed.prefetched[reader] == readable, observed.why()
