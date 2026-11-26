"""Drive the durable path with the same recorded, scriptable I/O as ``legacy_driver``.

The replay corpus compares harnesses on one contract:
a ``DriveSpec`` in, a ``DriveResult`` (``Recording``, tool-boundary stats,
the world's effects) out. This driver runs the run the way the harness worker
does: an admitted run in an in-memory journal with a pinned spec, the real
``PipesHubRuntimeAssembler`` (sources, hooks, pre-run steps, turn shaper,
presenter) and ``RunDriver``. Only the outermost I/O is faked, and it is the
same fakes the legacy driver installs: the model transport (recorded), the
toolset catalog (the test world's toolsets), the artifact registry and the
graph/config stores.

Durable runs agent chat in ``quick`` and ``react`` (P1a) and the built-in
assistant's ``/chat/stream`` modes (P1b); anything else raises ``Unsupported``
(Universal Agent Mode, planExecute and deep need more of the harness).
"""

from __future__ import annotations

import os
import time
from contextlib import ExitStack
from dataclasses import fields, replace
from typing import TYPE_CHECKING, Any, override
from unittest.mock import AsyncMock, patch

from app.harness.testing_adapters import (
    RecordingTransport,
    ScriptedModelTransport,
    tool_boundary,
)
from harness_core.journal.memory import LegacyImport
from harness_core.kernel.core.tool_content import UNKNOWN_TOOL_ERROR_PREFIX
from harness_core.testing import ManualClock, ScriptedModel
from harness_core.testing.replay import (
    FinalOutcome,
    FrozenTools,
    Manifest,
    ModelStep,
    RecordingModel,
    RunInputs,
    RunRecorder,
    ToolStep,
    recording_to_script,
)
from tests.contracts.golden_support import (
    GOLDEN_RECORD_IDS,
    USER_ID,
    _FakeArtifactRegistry,
    answer_people_lookups,
    owning,
)
from tests.harness_replay.knowledge_base import (
    KNOWLEDGE_SOURCE_ID,
    FixtureKnowledgeBase,
    fixture_knowledge,
    record_id,
)
from tests.harness_replay.legacy_driver import (
    AGENT_ROUTE,
    CHAT_ROUTE,
    DriveResult,
    DriveSpec,
    _prefetch_for,
    _Registry,
    is_external_tool,
)
from tests.harness_replay.world import ReplayWorld, WorldFixtures, activate, deactivate
from tests.support.knowledge_builder import builder_patches
from tests.support.record_access import InMemoryRecordAccess

if TYPE_CHECKING:
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.model import ResolvedModel
    from harness_core.journal import JournalEvent
    from harness_core.testing.model_types import ModelRequest, ModelResponse
    from harness_core.testing.replay import Recording

DURABLE_MODES = frozenset({"auto", "quick", "react"})
# The built-in assistant's modes Node admits durably (`assistantChatModes`).
DURABLE_CHAT_MODES = frozenset({"internal_search", "web_search"})
RUN = "run-eval"
SESSION = "ses-eval"
MAX_RECLAIMS = 3
RECLAIM_AFTER_S = 60.0


class Unsupported(ValueError):
    """The durable P1a path does not run this route or mode."""


def supports(route: str, chat_mode: str) -> bool:
    if route == CHAT_ROUTE:
        return chat_mode in DURABLE_CHAT_MODES
    return route == AGENT_ROUTE and chat_mode in DURABLE_MODES


def _chat_services(spec: DriveSpec) -> Any:  # noqa: ANN401 - world.Services
    """The legacy chat driver's world: the user's email and no names (the route
    cannot use the graph's user document), no org document, and the fixtures'
    knowledge as the retrieval's hits."""
    from tests.unit.harness.assembly import world as w

    services = w.Services(agent={})
    # Its graph key too: the turn scope and the access check resolve the user by it (main #4255).
    services.graph.get_user_by_user_id = AsyncMock(
        return_value={"_key": "ukey-1", "userId": USER_ID, "email": "user@corp.example"}
    )
    services.org_document = None
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {
        "status_code": 200,
        "searchResults": [{"metadata": {"virtualRecordId": d.id}} for d in spec.fixtures.knowledge],
        "virtual_to_record_map": {},
    }
    services.retrieval = retrieval
    return services


def _services(spec: DriveSpec) -> Any:  # noqa: ANN401 - world.Services
    """The legacy driver's identity: the user's email, no name, no org name. The agent's knowledge holds the
    fixture source when the run searches it: Node pins only the agent's own sources (main #4255 bounds by them)."""
    from tests.unit.harness.assembly import world as w

    services = w.Services(
        agent=w.agent_doc(
            toolsets=[], mcpServers=[],
            knowledge=[*fixture_knowledge(spec.knowledge_search), *(dict(k) for k in spec.tenant_knowledge)],
            systemPrompt=None, instructions=None,
        )
    )
    services.graph.get_user_by_user_id = AsyncMock(
        return_value={"_key": "ukey-1", "userId": USER_ID, "email": "user@corp.example"}
    )
    services.org_document = {"accountType": "enterprise"}
    return services


def _pinned(spec: DriveSpec) -> Any:  # noqa: ANN401 - RunSpec
    from harness_core.contracts import dump_contract, validate_contract
    from harness_core.contracts.generated.run_spec import RunSpec
    from tests.unit.harness.assembly import world as w

    model_name = spec.model_name
    pinned = (
        w.pinned_spec(
            tool_ids=["web@default/web_search"] if spec.chat_mode == "web_search" else [],
            kb=[],
            agent=False,
            chat={"mode": spec.chat_mode, "internalSearch": spec.chat_mode == "internal_search"},
            strict_scope=False,
        )
        if spec.route == CHAT_ROUTE
        else w.pinned_spec(
            tool_ids=[],
            kb=[KNOWLEDGE_SOURCE_ID] if spec.knowledge_search else [],
            apps=[k["connectorId"] for k in spec.tenant_knowledge],
            sandbox=spec.sandbox_profile,
        )
    )
    raw = dump_contract(pinned)
    if spec.principal is not None:
        raw["principalRef"] = spec.principal
    raw["model"]["policy"][0]["model"] = model_name
    if spec.selection:
        # Node pins the selection as asked (SYNC4 S1); a request that selects leaves out the kinds it names none of.
        raw["extensions"]["x.pipeshub"]["knowledge"].update(
            {key: sorted(set(ids)) for key, ids in spec.selection.items() if ids}
        )
    raw["extensions"]["x.pipeshub"]["model"]["modelName"] = model_name
    return validate_contract(RunSpec, raw)


class _Attach:
    """``InMemoryJournal`` claims carry no run snapshot: attach the admitted one."""

    def __init__(self, inner: Any, spec: DriveSpec) -> None:  # noqa: ANN401
        self._inner = inner
        self._spec = spec
        # Node stamps a run at admission; a live model is shown that time, not the epoch.
        self._admitted_at = int(time.time() * 1000)

    async def build(self, claim: Any) -> Any:  # noqa: ANN401
        from tests.unit.harness.assembly import world as w

        node = w.claim(
            claim.spec,
            query=self._spec.query,
            run_id=RUN,
            chat_mode=self._spec.chat_mode,
            timezone=self._spec.timezone,
            current_time=self._spec.current_time,
            previous=list(self._spec.previous_turns) or None,
            filters=self._spec.request_filters(),
            created_at=self._admitted_at,
        )
        run = node.run
        if self._spec.include_retrieval_context and run is not None and run.input is not None:
            run = run.model_copy(update={"input": run.input.model_copy(update={"include_retrieval_context": True})})
        values = {f.name: getattr(claim, f.name) for f in fields(claim)}
        return await self._inner.build(type(node)(**values, run=run))


def _decide_held(journal: Any, decide: Any) -> None:  # noqa: ANN401 - the in-memory journal; DriveSpec.decide
    """A person decides each call the suspended run holds, from what its approval card shows (the
    ``interaction.requested`` event)."""
    asked = {
        str(e.data.get("interactionId")): dict(e.data)
        for e in journal.events(SESSION)
        if e.type == "interaction.requested" and e.run_id == RUN
    }
    for interaction in journal.open_interactions(RUN):
        journal.resolve_interaction(RUN, interaction, decide(asked.get(interaction, {})))


def _final(events: list[JournalEvent]) -> FinalOutcome:
    """The legacy parse of the run's end (``legacy_driver.parse_final``), from the journal."""
    finalized = [e.data for e in events if e.type == "x.pipeshub.answer.finalized"]
    ended = next((e for e in reversed(events) if e.type.startswith("run.") and e.type in (
        "run.completed", "run.failed", "run.cancelled", "run.quarantined",
    )), None)
    if not finalized:
        return FinalOutcome(answer="", status="error" if ended is not None else "no_result")
    answer = finalized[-1]
    outcome = str(answer.get("outcome"))
    match_type = answer.get("answerMatchType")
    if outcome == "stopped":
        status = "stopped"
    elif outcome == "needs_input":
        status = "needs_input"
    elif match_type == "Error" or outcome == "error":
        status = "error"
    elif match_type == "Fallback Response":
        status = "fallback"
    else:
        status = "completed"
    # ADR-RUN-01 D1 / WP-89, WP-100: a closing beside prose (a flagged partial) is the durable loop's addition, not the
    # answer, so it is not compared; with no prose at all (nothing done before a cut) the closing is the answer.
    text = str(answer.get("answer") or "")
    if not text.strip():
        settled = [e.data for e in events if e.type == "x.pipeshub.outcome.settled" and e.run_id == RUN]
        text = str((settled[-1].get("presented") or {}).get("closing") or "") if settled else ""
    return FinalOutcome(answer=text, status=status)


async def _outcome_errors(events: list[JournalEvent], sealed: Any) -> dict[str, str]:  # noqa: ANN401
    """Each call's journaled error text, by call id, read through the run's content route."""
    from app.harness.adapters.content_store import PipesHubContentStore
    from harness_core.contracts.generated.common import ContentRef
    from harness_core.kernel.durable.transcript import decode_result, result_text

    content = PipesHubContentStore(sealed)
    calls: dict[str, str] = {}
    for event in events:
        if event.type == "model.response":
            turn = sum(1 for e in events[: events.index(event) + 1] if e.type == "model.response")
            for index, call in enumerate(event.data.get("toolCalls") or []):
                calls[f"t{turn}.{index}"] = str(call["callId"])
    errors: dict[str, str] = {}
    for event in events:
        if event.type != "tool.outcome" or event.data.get("status") == "ok":
            continue
        call_id = calls.get(str(event.data["invocationId"]))
        if call_id is not None:
            value, _ = decode_result(await content.get(ContentRef.model_validate(event.data["resultRef"])))
            errors[call_id] = result_text(value)
    return errors


class _WithoutPlanTool(RunRecorder):
    """UXR-B1's allowed difference (``plan_tool``): quick, react and auto offer write_todos (R49-PLAN), today's loop never did. The
    recording leaves it out, so a legacy recording still digests equal and any other difference still shows."""

    @override
    def model(self, request: ModelRequest, response: ModelResponse | None = None, error: BaseException | None = None) -> None:
        tools = tuple(t for t in request.tools if t != "write_todos")
        super().model(replace(request, tools=tools), response, error)


def _record_refused(recorder: RunRecorder, errors: dict[str, str]) -> None:
    """A call the kernel settles itself (a tool the run does not have) never
    reaches the executor the boundary wraps; legacy records it as a failed
    step, so the durable recording does too, with the error the run journaled."""
    steps = recorder.ordered_steps()
    executed = {s.call_id for s in steps if isinstance(s, ToolStep)}
    for step in steps:
        if not isinstance(step, ModelStep) or step.response is None:
            continue
        for call in step.response.tool_calls:
            if call.id in executed or call.name in step.request.tools:
                continue
            recorder.tool(
                call_id=call.id,
                name=call.name,
                args=call.args,
                status="failed",
                error=errors.get(call.id, f"{UNKNOWN_TOOL_ERROR_PREFIX} {call.name}"),
            )


async def drive(spec: DriveSpec) -> DriveResult:
    """Run one scenario through the durable path and record it."""
    from app.harness.assembly.assembler import PipesHubRuntimeAssembler
    from app.harness.assembly.model import ClassifiedModelPort, ResolvedModel
    from harness_core.driver import RunDriver
    from harness_core.kernel.durable.adapters import StreamingModelPort
    from harness_core.models.transport_model import TransportModel
    from tests.unit.agents.adapter.conftest import FakeChatModel
    from tests.unit.harness.assembly import world as w

    if not supports(spec.route, spec.chat_mode):
        raise Unsupported(f"{spec.route}/{spec.chat_mode} is not a durable run")
    recorder = _WithoutPlanTool(scrub=spec.scrub)
    world = ReplayWorld(fixtures=spec.fixtures)
    if spec.transport is not None:
        transport: Any = RecordingTransport(spec.transport, recorder)
    else:
        if spec.script is None:
            raise ValueError(f"{spec.scenario}: a scripted run needs a script")
        transport = ScriptedModelTransport(
            RecordingModel(ScriptedModel(spec.script), recorder), model_name=spec.model_name
        )

    wire = spec.transport.provider if spec.transport is not None else "scripted"

    async def model_factory(run: RunContext, services: object) -> ResolvedModel:
        return ResolvedModel(
            raw_llm=FakeChatModel(),
            provider="openai",
            is_multimodal=False,
            context_length=spec.context_length,
            is_reasoning=spec.is_reasoning,
            port=ClassifiedModelPort(
                # The journal names the transport that reported the usage (the ladder reads it by
                # it): a live run's is LangChain's, which counts cached reads and writes in input.
                StreamingModelPort(TransportModel(transport), provider="openai", transport=wire),
            ),
            transport=transport,
        )

    services = _chat_services(spec) if spec.route == CHAT_ROUTE else _services(spec)
    services.entity_store = spec.entity_store
    knowledge_patches: list[tuple[str, object]] = []
    knowledge: FixtureKnowledgeBase | None = None
    # The full-record read gate asks the graph again (H1): the run's actor owns exactly what the legacy driver's
    # user owns, the goldens' records and, with the knowledge search, every fixture record, on either route (DUR3:
    # the assistant's route had no answer, so a live model's fetch there read a mock as a storage error).
    owned = (*GOLDEN_RECORD_IDS, *(record_id(doc) for doc in world.fixtures.knowledge if spec.knowledge_search))
    access = spec.record_access if spec.record_access is not None else owning(InMemoryRecordAccess(), w.ACTOR, w.ORG, owned)
    access.serve(services.graph)
    answer_people_lookups(services.graph, w.ACTOR)
    if spec.knowledge_search:
        retrieval = AsyncMock()
        services.retrieval = retrieval
        knowledge = FixtureKnowledgeBase(
            tuple(world.fixtures.knowledge), snippets=spec.knowledge_snippets,
            connectors=spec.knowledge_connectors, searchable=spec.knowledge_searchable,
        )
        knowledge_patches = knowledge.patches(retrieval)
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock)
    # Earlier turns the journal does not hold reach a durable run the one way the product gives them: Node journals
    # them as `session.imported` before the run (P1b T3). The claim's `previousConversations` alone never reach
    # the model's messages, so a driven run without this saw no history (DUR1).
    imported = LegacyImport(list(spec.previous_turns), len(spec.previous_turns)) if spec.previous_turns else None
    journal.admit(
        RUN, org_id=w.ORG, session_id=SESSION, spec=_pinned(spec), input_text=spec.query, imported=imported,
        **({"principal_ref": spec.principal} if spec.principal is not None else {}),
    )
    env = {
        "PIPESHUB_ENABLE_CODE_EXECUTION": "false",
        "PIPESHUB_ENABLE_SKILLS": "false",
        "PIPESHUB_AGENT_TRANSPORT": "langchain",
        **spec.env,
    }
    token = activate(world)
    started = time.monotonic()
    try:
        with ExitStack() as stack:
            for target, value in (
                (
                    "app.agents.registry.toolset_registry.get_toolset_registry",
                    lambda: _Registry(spec.fake_toolsets, spec.knowledge_search),
                ),
                (
                    "app.services.artifact_registry.ArtifactRegistryService",
                    spec.artifacts if spec.artifacts is not None else _FakeArtifactRegistry,
                ),
                *builder_patches("app.agents.chat_modes.prefetch.KnowledgeContextBuilder", _prefetch_for(world)),
                *knowledge_patches,
            ):
                stack.enter_context(patch(target, new=value))
            stack.enter_context(patch.dict(os.environ, env))
            stats = stack.enter_context(
                tool_boundary(recorder, spec.frozen, spec.approve, freeze=is_external_tool)
            )
            driver = RunDriver(
                journal=journal,
                assembler=_Attach(PipesHubRuntimeAssembler(services, model_factory=model_factory), spec),
                clock=clock,
            )
            claim = await journal.claim(RUN, worker_id="eval-worker")
            assert claim is not None
            done = await driver.run_claimed(claim)
            # A yielded run (a provider's rate limit) is claimed again once its
            # backoff has passed, as the sweeper and the claimers do.
            for _ in range(MAX_RECLAIMS):
                if done.status == "suspended" and spec.decide is not None:
                    _decide_held(journal, spec.decide)
                elif done.status == "yielded":
                    _ = clock.advance(RECLAIM_AFTER_S)
                    _ = journal.sweep()
                else:
                    break
                claim = await journal.claim(RUN, worker_id="eval-worker")
                if claim is None:
                    break
                done = await driver.run_claimed(claim)
    finally:
        deactivate(token)
    latency = time.monotonic() - started
    events = journal.events(SESSION)
    _record_refused(recorder, await _outcome_errors(events, journal.content))
    recording = recorder.finish(
        Manifest(scenario=spec.scenario, model=spec.model_name),
        inputs=RunInputs(
            route=spec.route,
            chat_mode=spec.chat_mode,
            query=spec.query,
            fixtures=spec.fixtures.model_dump(mode="json"),
        ),
        final=_final(events),
        effects=world.effects(),
    )
    return DriveResult(
        recording, "", stats, world, latency, spec.frozen, events=events,
        shown_records=frozenset(knowledge.shown) if knowledge is not None else None,
    )


async def replay(recording: Recording, **overrides: Any) -> DriveResult:  # noqa: ANN401
    """``strict`` replay of a (legacy) recording through the durable path."""
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


__all__ = ["DURABLE_CHAT_MODES", "DURABLE_MODES", "Unsupported", "drive", "replay", "supports"]
