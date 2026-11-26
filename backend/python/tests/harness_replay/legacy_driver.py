"""Drive the legacy chat and agent routes with recorded, scriptable I/O.

The same seams the P0.7 golden contract uses (``tests/contracts/golden_support``)
are faked here: the model transport, the toolset catalog, artifact registry,
retrieval prefetch and the graph/config stores. Everything between them —
``PipesHubAgentFactory``, the loop strategies, hooks, ``ToolExecutor``, the
answer finalizer and the SSE bridges — runs for real.

Three things are recorded into one :class:`Recording`:

* every model call, through :class:`RecordingModel` (scripted) or
  :class:`RecordingTransport` (a real model, in a recorded live run);
* every tool execution, at ``ToolExecutor`` via :func:`tool_boundary`;
* every external effect the fake connectors committed, plus the final answer
  parsed from the SSE stream.

:func:`replay` re-drives a recording with its own model responses and frozen
tool outcomes, which is the ``strict`` replay mode of TEST-STRATEGY §4.6.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.actions.internal_tools.intrim_tools import InternalTools
from app.agents.agent_loop.cancellation.in_process import (
    InProcessRunCancellationRegistry,
)
from app.harness.testing_adapters import (
    RecordingTransport,
    ScriptedModelTransport,
    ToolBoundaryStats,
    tool_boundary,
)
from harness_core.testing import ScriptedModel, Usage
from harness_core.testing.replay import (
    FinalOutcome,
    FrozenTools,
    Manifest,
    Recording,
    RecordingModel,
    RunInputs,
    RunRecorder,
    recording_to_script,
)
from tests.contracts.golden_support import (
    CLIENT_NAME,
    CONVERSATION_ID,
    GOLDEN_RECORD_IDS,
    ORG_ID,
    USER_ID,
    _config_service,
    _FakeArtifactRegistry,
    _graph_provider,
    _mock_request,
    _record,
)
from tests.harness_replay.knowledge_base import (
    KNOWLEDGE_SOURCE_ID,
    FixtureKnowledgeBase,
    fixture_knowledge,
    record_id,
)
from tests.harness_replay.world import (
    TOOLSETS,
    ReplayWorld,
    WorldFixtures,
    activate,
    deactivate,
)
from tests.support.bound_org import another_request
from tests.support.knowledge_builder import builder_patches

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.agent_loop_lib.core.types import ToolCall as CoreToolCall
    from app.agent_loop_lib.transport.base import LLMTransport
    from harness_core.testing import ModelScript
    from tests.harness_replay.artifacts import ParityArtifactRegistry

RUN_ID = "5d0c3f6e-7a41-4b1c-9d2e-3f4a5b6c7d8e"
AGENT_ROUTE = "agent"
CHAT_ROUTE = "chat"


@dataclass
class DriveSpec:
    scenario: str
    query: str
    route: str = AGENT_ROUTE
    chat_mode: str = "quick"
    script: ModelScript | None = None
    transport: LLMTransport | None = None
    fixtures: WorldFixtures = field(default_factory=WorldFixtures)
    frozen: FrozenTools | None = None
    approve: Callable[[CoreToolCall, str], Awaitable[bool]] | None = None
    env: dict[str, str] = field(default_factory=dict)
    model_name: str = "scripted-model"
    scrub: Callable[[str], str] | None = None
    # Serve ``fixtures.knowledge`` through the product's knowledgegraph search
    # tool (and so its citations), not only the fake ``knowledge`` toolset.
    knowledge_search: bool = False
    # Search shows a few blocks of each record, not the whole record (``FixtureKnowledgeBase.snippets``).
    knowledge_snippets: bool = False
    # Which fake connector toolsets (``world.TOOLSETS`` keys) the agent gets;
    # None = all of them, the replay and P0.3 default.
    fake_toolsets: tuple[str, ...] | None = None
    artifacts: ParityArtifactRegistry | None = None
    previous_turns: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    # The durable path's pinned ``grants.sandboxProfile`` (P5.11: ``agent-code`` gives it ``run_code``).
    sandbox_profile: str = "none"
    # The knowledge-graph entity store the request carries (main #3308); ``None``, as with no entities collection.
    entity_store: Any = None
    # The org's model entry (``contextLength``, ``isReasoning``) both routes take the prompt's model tier from,
    # and what the client sends with the request (``timezone``, ``currentTime``): every driver passes them the
    # same way, so two routes compared on one spec are shown one prompt (DUR1).
    context_length: int = 128_000
    is_reasoning: bool = False
    timezone: str | None = None
    current_time: str | None = None
    # The request's `includeRetrievalContext`: today's loop's measurement frames (SYNC2-F1).
    include_retrieval_context: bool = False
    # The request's selection below app level (main #4255: `recordGroups`/`records`/`recordsExact`), beside the
    # knowledge source when the run searches it. Legacy gets it in the request's filters, the durable route in
    # its pin and admitted input.
    selection: dict[str, list[str]] | None = None
    # A seeded tenant (G-49), durable route only: the graph's record access the run's actor is decided by (in place
    # of "the actor owns every fixture record"), each knowledge doc's connector, which docs the actor's search finds,
    # the run's principal (``run.admitted``), and how a person decides each call the run holds for approval.
    record_access: Any = None
    knowledge_connectors: dict[str, str] = field(default_factory=dict[str, str])
    knowledge_searchable: Callable[[str], bool] | None = None
    principal: str | None = None
    decide: Callable[[dict[str, Any]], str] | None = None
    # The tenant's own connector instances as agent knowledge beside the fixture source (EV-SAF-03), durable route
    # only: reads by id are bounded by the agent's sources, and the entries' connector types are what the run knows.
    tenant_knowledge: tuple[dict[str, str], ...] = ()

    def request_filters(self) -> dict[str, Any] | None:
        """The filters the client sends: the knowledge source when searched, and the selection."""
        filters: dict[str, Any] = {"kb": [KNOWLEDGE_SOURCE_ID]} if self.knowledge_search else {}
        if self.selection:
            filters = {"apps": [], "kb": [], **filters, **self.selection}
        return filters or None


SANDBOX_TOOLS = frozenset({"run_code", "install_packages", "read_sandbox_file"})

# What a user sees move: a step, text, reasoning or a tool card. RUN_STARTED,
# state snapshots and heartbeats render nothing, so they are not progress.
PROGRESS_EVENTS = frozenset(
    {"STEP_STARTED", "TEXT_MESSAGE_CONTENT", "REASONING_MESSAGE_CONTENT", "TOOL_CALL_START"}
)


def is_external_tool(name: str) -> bool:
    """The outside world a replay freezes: fake connectors and the sandbox."""
    return name in SANDBOX_TOOLS or name.split("__", 1)[0] in TOOLSETS


@dataclass
class DriveResult:
    recording: Recording
    sse: str
    stats: ToolBoundaryStats
    world: ReplayWorld
    latency_s: float
    frozen: FrozenTools | None = None
    first_progress_s: float | None = None
    # The durable driver's session journal (``None`` from the legacy route, which has none).
    events: list[Any] | None = None
    # Fixture doc ids the run's knowledge tools rendered to it (``None``: no fixture knowledge base served it).
    shown_records: frozenset[str] | None = None

    @property
    def error_code(self) -> str | None:
        """The code the run ended with: a durable run's ``run.failed`` errorType, the legacy route's RUN_ERROR."""
        if self.events is not None:
            failed = [e for e in self.events if getattr(e, "type", None) == "run.failed"]
            return (getattr(failed[-1], "data", None) or {}).get("errorType") if failed else None
        for line in self.sse.split("\n"):
            if line.startswith("data: ") and '"RUN_ERROR"' in line:
                try:
                    return json.loads(line[len("data: "):]).get("code")
                except json.JSONDecodeError:
                    continue
        return None

    @property
    def usage(self) -> Usage:
        total = Usage()
        for step in self.recording.model_steps():
            if step.response is not None:
                total = total + step.response.usage
        return total


class _Registry:
    def __init__(
        self, fake_toolsets: tuple[str, ...] | None = None, knowledge_graph: bool = False
    ) -> None:
        self._fake = tuple(TOOLSETS) if fake_toolsets is None else fake_toolsets
        self._knowledge_graph = knowledge_graph

    def get_all_toolsets(self) -> dict[str, dict[str, Any]]:
        catalog: dict[str, dict[str, Any]] = {
            "internaltools": {
                "class": InternalTools,
                "isInternal": True, "provider": "platform",
                "essential": True,
                "description": "Internal tools",
            },
        }
        # Keys are prefixed so the product's per-connector client factories
        # (keyed by "slack", "jira", ...) never try to build a real client;
        # tool names still come from each class's path ("slack__send_message").
        for name in self._fake:
            cls, description = TOOLSETS[name]
            catalog[f"eval{name}"] = {
                "class": cls,
                "isInternal": True, "provider": "platform",
                "essential": False,
                "description": description,
            }
        if self._knowledge_graph:
            from app.agents.actions.knowledge_graph.knowledge_graph import (
                KnowledgeGraph,
            )

            catalog["knowledgegraph"] = {
                "class": KnowledgeGraph,
                "isInternal": True, "provider": "platform",
                "essential": True,
                "description": "Company knowledge",
            }
        return catalog


def _flat(vrid: str, index: int, content: str, title: str) -> dict[str, Any]:
    return {
        "virtual_record_id": vrid,
        "block_index": index,
        "content": content,
        "block_type": "text",
        "metadata": {
            "virtualRecordId": vrid,
            "recordId": f"rec-{vrid}",
            "recordName": title,
            "orgId": ORG_ID,
            "mimeType": "text/markdown",
            "origin": "UPLOAD",
            "connector": "KB",
            "recordType": "FILE",
            "blockIndex": index,
        },
        "score": 0.9,
    }


def _prefetch_for(world: ReplayWorld) -> Callable[..., Awaitable[list[dict[str, Any]]]]:
    async def fake(
        search_results: list[dict[str, Any]],
        blob_store: object,
        org_id: str,
        is_multimodal_llm: bool,
        virtual_record_id_to_result: dict[str, Any],
        virtual_to_record_map: dict[str, Any],
        **_: object,
    ) -> list[dict[str, Any]]:
        out = []
        for doc in world.fixtures.knowledge:
            virtual_record_id_to_result[doc.id] = _record(
                doc.id, f"rec-{doc.id}", doc.title
            )
            out.append(_flat(doc.id, 0, doc.text, doc.title))
        return out

    return fake


def is_progress(chunk: str) -> bool:
    for line in chunk.split("\n"):
        if not line.startswith("data: "):
            continue
        try:
            kind = json.loads(line[len("data: ") :]).get("type")
        except (json.JSONDecodeError, AttributeError):
            continue
        if kind in PROGRESS_EVENTS:
            return True
    return False


def _scan(sse: str) -> tuple[dict[str, Any] | None, bool]:
    finished: dict[str, Any] | None = None
    errored = False
    for block in sse.split("\n\n"):
        for line in block.split("\n"):
            if not line.startswith("data: "):
                continue
            try:
                data = json.loads(line[len("data: ") :])
            except json.JSONDecodeError:
                continue
            if data.get("type") == "RUN_FINISHED":
                finished = data.get("result") or {}
            elif data.get("type") == "RUN_ERROR":
                errored = True
    return finished, errored


def final_result(sse: str) -> dict[str, Any]:
    """The ``RUN_FINISHED`` result payload (answer, citations, ...), or ``{}``."""
    return _scan(sse)[0] or {}


def parse_final(sse: str) -> FinalOutcome:
    finished, errored = _scan(sse)
    if finished is None:
        return FinalOutcome(answer="", status="error" if errored else "no_result")
    answer = str(finished.get("answer") or "")
    status = finished.get("status")
    if status == "waiting_input":
        # main #3592: the route now says so itself, with no fallback answer; the rig's name for it.
        status = "needs_input"
    if not status:
        match_type = finished.get("answerMatchType")
        if "internaltools__ask_user_question" in (
            finished.get("succeeded_tool_names") or []
        ):
            status = "needs_input"
        elif match_type == "Error":
            status = "error"
        elif match_type == "Fallback Response":
            status = "fallback"
        else:
            status = "completed"
    return FinalOutcome(answer=answer, status=str(status))


async def drive(spec: DriveSpec) -> DriveResult:
    """Run one scenario through the legacy route and record it."""
    from app.agents.agent_loop.stream_bridge import run_agent_loop_stream
    from app.api.routes.chatbot import askAIStream

    recorder = RunRecorder(scrub=spec.scrub)
    world = ReplayWorld(fixtures=spec.fixtures)
    if spec.transport is not None:
        transport: Any = RecordingTransport(spec.transport, recorder)
    else:
        if spec.script is None:
            raise ValueError(f"{spec.scenario}: a scripted run needs a script")
        transport = ScriptedModelTransport(
            RecordingModel(ScriptedModel(spec.script), recorder),
            model_name=spec.model_name,
        )
    registry = InProcessRunCancellationRegistry()
    llm_config = {
        "provider": "openai",
        "isMultimodal": False,
        "contextLength": spec.context_length,
        "isReasoning": spec.is_reasoning,
    }
    client_time: dict[str, Any] = {
        key: value
        for key, value in (("timezone", spec.timezone), ("currentTime", spec.current_time))
        if value is not None
    }
    if spec.include_retrieval_context:
        client_time["includeRetrievalContext"] = True
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {
        "status_code": 200,
        "searchResults": [
            {"metadata": {"virtualRecordId": d.id}} for d in spec.fixtures.knowledge
        ],
        "virtual_to_record_map": {},
    }
    patches: list[tuple[str, object]] = []
    # The fixture's user owns every record of the world's knowledge, and the goldens' own.
    owned: tuple[str, ...] = GOLDEN_RECORD_IDS
    knowledge: FixtureKnowledgeBase | None = None
    if spec.knowledge_search:
        knowledge = FixtureKnowledgeBase(tuple(world.fixtures.knowledge), snippets=spec.knowledge_snippets)
        patches += knowledge.patches(retrieval)
        owned += tuple(record_id(doc) for doc in knowledge.docs)
    env = {
        "PIPESHUB_ENABLE_CODE_EXECUTION": "false",
        "PIPESHUB_ENABLE_SKILLS": "false",
        "PIPESHUB_AGENT_TRANSPORT": "langchain",
        **spec.env,
    }

    token = activate(world)
    started = time.monotonic()
    first_progress: float | None = None

    def seen(chunk: str | bytes) -> str | bytes:
        nonlocal first_progress
        if first_progress is None:
            text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", "replace")
            if is_progress(text):
                first_progress = time.monotonic() - started
        return chunk

    try:
        with ExitStack() as stack:
            for target, value in (
                (
                    "app.agents.agent_loop.factory.LangChainTransport",
                    lambda *a, **k: transport,
                ),
                (
                    "app.agents.registry.toolset_registry.get_toolset_registry",
                    lambda: _Registry(spec.fake_toolsets, spec.knowledge_search),
                ),
                (
                    "app.api.routes.chatbot.get_llm_for_chat",
                    AsyncMock(return_value=(MagicMock(), llm_config, {})),
                ),
                (
                    "app.services.artifact_registry.ArtifactRegistryService",
                    spec.artifacts if spec.artifacts is not None else _FakeArtifactRegistry,
                ),
                *builder_patches("app.agents.chat_modes.prefetch.KnowledgeContextBuilder", _prefetch_for(world)),
                *patches,
            ):
                stack.enter_context(patch(target, new=value))
            stack.enter_context(patch.dict(os.environ, env))
            # Both legacy routes run behind the auth middleware, which binds the token's org for the request.
            stack.enter_context(another_request(ORG_ID, USER_ID))
            stats = stack.enter_context(
                tool_boundary(
                    recorder, spec.frozen, spec.approve, freeze=is_external_tool
                )
            )

            if spec.route == CHAT_ROUTE:
                body = {
                    "query": spec.query,
                    "chatMode": spec.chat_mode,
                    "protocol": "agui",
                    "runId": RUN_ID,
                    "conversationId": CONVERSATION_ID,
                    **client_time,
                    **({"filters": spec.request_filters()} if spec.selection else {}),
                }
                response = await askAIStream(
                    request=_mock_request(body),
                    retrieval_service=retrieval,
                    graph_provider=_graph_provider(owned),
                    config_service=_config_service(),
                    cancellation_registry=registry,
                )
                chunks = [seen(chunk) async for chunk in response.body_iterator]
            else:
                query_info: dict[str, Any] = {
                    "query": spec.query,
                    "chatMode": spec.chat_mode,
                    "runId": RUN_ID,
                    "conversationId": CONVERSATION_ID,
                    "agentToolsets": [],
                    **client_time,
                }
                if spec.knowledge_search:
                    query_info["filters"] = {"kb": [KNOWLEDGE_SOURCE_ID]}
                    query_info["knowledge"] = list(fixture_knowledge(True))
                if spec.selection:
                    # What the agent route does before the loop (main #4255 `_resolve_turn_filters`).
                    query_info["filters"] = await _route_turn_filters(spec, query_info, _graph_provider(owned))
                if spec.previous_turns:
                    query_info["previousConversations"] = list(spec.previous_turns)
                user_info = {
                    "userId": USER_ID,
                    "orgId": ORG_ID,
                    "email": "user@corp.example",
                }
                chunks = [
                    seen(chunk)
                    async for chunk in run_agent_loop_stream(
                        query_info,
                        user_info,
                        MagicMock(),
                        _quiet_logger(),
                        retrieval,
                        _graph_provider(owned),
                        None,
                        _config_service(),
                        protocol="agui",
                        model_name=spec.model_name,
                        model_key="eval-model",
                        llm_provider="openai",
                        context_length=spec.context_length,
                        is_reasoning_model=spec.is_reasoning,
                        client_name=CLIENT_NAME,
                        cancellation_registry=registry,
                        entity_vector_store=spec.entity_store,
                    )
                ]
    finally:
        deactivate(token)
    latency = time.monotonic() - started
    sse = "".join(c if isinstance(c, str) else c.decode("utf-8") for c in chunks)
    recording = recorder.finish(
        Manifest(scenario=spec.scenario, model=spec.model_name),
        inputs=RunInputs(
            route=spec.route,
            chat_mode=spec.chat_mode,
            query=spec.query,
            fixtures=spec.fixtures.model_dump(mode="json"),
        ),
        final=parse_final(sse),
        effects=world.effects(),
    )
    return DriveResult(
        recording, sse, stats, world, latency, spec.frozen, first_progress_s=first_progress,
        shown_records=frozenset(knowledge.shown) if knowledge is not None else None,
    )


async def _route_turn_filters(spec: DriveSpec, query_info: dict[str, Any], graph: Any) -> dict[str, Any]:  # noqa: ANN401
    from app.api.routes.agent import _resolve_turn_filters

    return await _resolve_turn_filters(
        agent_id="agent-1",
        agent_knowledge=list(query_info.get("knowledge") or []),
        requested_filters=spec.request_filters(),
        graph_provider=graph,
        caller_user_id=USER_ID,
        org_id=ORG_ID,
        logger=_quiet_logger(),
        retrieval_user_id=USER_ID,
    )


def _quiet_logger() -> Any:  # noqa: ANN401 - logging.Logger
    import logging

    log = logging.getLogger("harness_replay.legacy")
    log.setLevel(logging.CRITICAL)
    return log


async def replay(recording: Recording, **overrides: Any) -> DriveResult:  # noqa: ANN401
    """``strict`` replay: the recording's model responses and tool outcomes."""
    spec = DriveSpec(
        scenario=recording.manifest.scenario,
        query=recording.inputs.query,
        route=recording.inputs.route,
        chat_mode=recording.inputs.chat_mode,
        script=recording_to_script(recording),
        fixtures=WorldFixtures.model_validate(recording.inputs.fixtures),
        frozen=FrozenTools(recording),
        model_name=recording.manifest.model,
    )
    for key, value in overrides.items():
        setattr(spec, key, value)
    return await drive(spec)


__all__ = [
    "AGENT_ROUTE",
    "CHAT_ROUTE",
    "KNOWLEDGE_SOURCE_ID",
    "PROGRESS_EVENTS",
    "SANDBOX_TOOLS",
    "DriveResult",
    "DriveSpec",
    "drive",
    "final_result",
    "is_external_tool",
    "is_progress",
    "parse_final",
    "replay",
]
