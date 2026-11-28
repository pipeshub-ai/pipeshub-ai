"""D-5 at journal level: the P0.7 agent scenarios, run durably, journal what the goldens show.

Each agent scenario's model script runs through ``RunDriver`` and the real
``PipesHubRuntimeAssembler`` (golden toolset catalog, real loader, real
tools) into the journal. The journal is projected to a neutral transcript
(tool calls with their arguments and status, the answer text, the outcome)
and compared with the same projection of the frozen legacy SSE golden.

What this cannot show is frame parity: AG-UI frames are the projector's,
pinned by the SSE goldens. Chat scenarios are P1b.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from contextlib import ExitStack
from dataclasses import dataclass, fields
from datetime import datetime, tzinfo
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import patch

import pytest

from app.harness.adapters.content_store import PipesHubContentStore
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.model import ClassifiedModelPort, ResolvedModel
from harness_core.contracts.generated.common import ContentRef
from harness_core.driver import RunDriver
from harness_core.failpoints import Arm, Nth, Raise
from harness_core.journal import LegacyImport
from harness_core.kernel.durable.adapters import StreamingModelPort
from harness_core.kernel.durable.transcript import (
    decode_assistant,
    decode_result,
    result_text,
)
from harness_core.models.transport_model import TransportModel
from harness_core.testing import ManualClock, armed
from tests.contracts import golden_support
from tests.contracts.golden_support import GOLDEN_DIR, GoldenTransport, Scenario
from tests.contracts.scenarios import SCENARIOS
from tests.unit.agents.adapter.conftest import FakeChatModel
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.harness.assembly.context import RunContext
    from harness_core.journal import InMemoryJournal, JournalEvent, LiveSinkPort
    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

RUN = "run-golden"
AGENT_SCENARIOS = [s for s in SCENARIOS if s.route == "agent"]
# The built-in assistant (`/chat/stream`). `chat_llm_error` is a rate limit,
# which a durable run retries instead of failing (L-18); its own test says what
# the stream shows instead.
CHAT_SCENARIOS = [s for s in SCENARIOS if s.route == "chat" and s.name != "chat_llm_error"]
DURABLE_SCENARIOS = AGENT_SCENARIOS + CHAT_SCENARIOS


@dataclass
class Transcript:
    tools: list[tuple[str, dict[str, Any], str]]
    answer: str
    outcome: str


def legacy_transcript(sse: str) -> Transcript:
    frames = []
    for block in sse.split("\n\n"):
        data = next((line[5:].strip() for line in block.splitlines() if line.startswith("data:")), None)
        if data:
            frames.append(json.loads(data))
    names: dict[str, str] = {}
    args: dict[str, str] = {}
    tools: list[tuple[str, dict[str, Any], str]] = []
    messages: dict[str, str] = {}
    order: list[str] = []
    outcome = "answered"
    for frame in frames:
        kind = frame["type"]
        if kind == "TOOL_CALL_START":
            names[frame["toolCallId"]] = frame["toolCallName"]
        elif kind == "TOOL_CALL_ARGS":
            args[frame["toolCallId"]] = args.get(frame["toolCallId"], "") + frame["delta"]
        elif kind == "TOOL_CALL_RESULT":
            call = frame["toolCallId"]
            status = "ok" if frame.get("status") == "completed" else "error"
            tools.append((names[call], json.loads(args.get(call) or "{}"), status))
        elif kind == "TEXT_MESSAGE_CONTENT":
            if frame["messageId"] not in messages:
                order.append(frame["messageId"])
            messages[frame["messageId"]] = messages.get(frame["messageId"], "") + frame["delta"]
        elif kind == "CUSTOM" and frame.get("name") == "ask_user_question":
            outcome = "needs_input"
        elif kind == "RUN_FINISHED" and (frame.get("result") or {}).get("status") == "stopped":
            outcome = "cancelled"
        elif kind == "RUN_ERROR" or (
            kind == "STATE_SNAPSHOT" and (frame.get("snapshot") or {}).get("answerMatchType") == "Error"
        ):
            outcome = "failed"
    answer = messages[order[-1]] if order and outcome == "answered" else ""
    return Transcript(tools, answer, outcome)


async def durable_transcript(events: list[JournalEvent], content: PipesHubContentStore) -> Transcript:
    async def blob(ref: Any) -> bytes:  # noqa: ANN401
        return await content.get(ContentRef.model_validate(ref))

    calls: dict[str, tuple[str, dict[str, Any]]] = {}
    tools: list[tuple[str, dict[str, Any], str]] = []
    invocations: dict[str, str] = {}
    answer = ""
    outcome = "failed"
    for event in events:
        data = event.data
        if event.type == "model.response":
            message = decode_assistant(await blob(data["messageRef"]))
            for index, call in enumerate(message.tool_calls or []):
                calls[call.id] = (call.name, dict(call.arguments))
                invocations[f"t{len([e for e in events[: events.index(event)] if e.type == 'model.response']) + 1}.{index}"] = call.id
        elif event.type == "tool.outcome":
            call_id = invocations[str(data["invocationId"])]
            content_value, is_error = decode_result(await blob(data["resultRef"]))
            _ = result_text(content_value)
            name, arguments = calls[call_id]
            tools.append((name, arguments, "error" if is_error or data["status"] != "ok" else "ok"))
        elif event.type == "message.completed" and data["role"] == "assistant" and data["isFinal"]:
            answer = (await blob(data["textRef"])).decode("utf-8")
        elif event.type == "run.completed":
            outcome = str(data["outcome"])
        elif event.type == "run.cancelled":
            outcome = "cancelled"
    return Transcript(tools, answer, outcome)


class _Attach:
    """InMemoryJournal claims carry no run snapshot; attach the admitted one."""

    def __init__(
        self,
        inner: PipesHubRuntimeAssembler,
        query: str,
        chat_mode: str,
        *,
        timezone: str | None,
        previous: list[dict[str, Any]] | None = None,
    ) -> None:
        self._inner = inner
        self._query = query
        self._chat_mode = chat_mode
        self._timezone = timezone
        self._previous = previous

    async def build(self, claim: Any) -> Any:  # noqa: ANN401
        node = w.claim(
            claim.spec,
            query=self._query,
            run_id=RUN,
            chat_mode=self._chat_mode,
            timezone=self._timezone,
            previous=self._previous,
        )
        values = {f.name: getattr(claim, f.name) for f in fields(claim)}
        return await self._inner.build(type(node)(**values, run=node.run))


def _golden_agent() -> dict[str, Any]:
    return w.agent_doc(toolsets=[], mcpServers=[], knowledge=[], systemPrompt=None, instructions=None)


def _chat_services() -> w.Services:
    """The legacy chat golden's world: the user's email and no names (the
    route reads them from the JWT and a user document it cannot use), no org
    document, and the golden's retrieval."""
    from unittest.mock import AsyncMock

    services = w.Services(agent={})
    user = {"userId": w.ACTOR, "email": "user@corp.example"}
    services.graph.get_user_by_user_id = AsyncMock(return_value=user)
    services.graph.get_document = AsyncMock(return_value=None)
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {
        "status_code": 200,
        "searchResults": [{"metadata": {"virtualRecordId": "vr-refund"}}],
        "virtual_to_record_map": {},
    }
    services.retrieval = retrieval
    return services


def _chat_spec(scenario: Scenario, executor: dict[str, Any] | None = None) -> Any:  # noqa: ANN401 - RunSpec
    """What Node pins for the built-in assistant in the scenario's mode."""
    web = scenario.chat_mode == "web_search"
    return w.pinned_spec(
        tool_ids=["web@default/web_search"] if web else [],
        kb=[],
        agent=False,
        chat={"mode": scenario.chat_mode, "internalSearch": scenario.chat_mode == "internal_search"},
        strict_scope=False,
        executor=executor,
    )


def _question_ids() -> SimpleNamespace:
    """Deterministic question ids, so a recorded journal is byte-stable."""
    counter = iter(range(1, 1_000_000))
    return SimpleNamespace(uuid4=lambda: uuid.UUID(int=next(counter)))


class _Crash(BaseException):
    """A worker dying at a failpoint: not an Exception, so no handler runs."""


@dataclass(frozen=True)
class Steer:
    """Node queues ``text`` into the running run (P1b T2): while the export
    tool runs, or when the model's first delta streams."""

    text: str
    when: Literal["export", "first_delta"]


class _SteeringBus:
    """Queues the steering input as the first live frame goes out, then
    passes every frame on (the model is streaming when the user sends)."""

    def __init__(self, journal: InMemoryJournal, text: str, inner: LiveSinkPort | None) -> None:
        self._journal = journal
        self._text = text
        self._inner = inner
        self._sent = False

    async def publish(self, frame: Any) -> None:  # noqa: ANN401 - LiveFrame
        if not self._sent:
            self._sent = True
            _ = self._journal.queue_input(RUN, self._text)
        if self._inner is not None:
            await self._inner.publish(frame)


async def _no_wait(seconds: float) -> None:
    """The kernel's retry and backoff waits, on the scenario's manual clock: no wall time."""
    del seconds
    await asyncio.sleep(0)


def _on_clock(clock: ManualClock) -> type[datetime]:
    """``datetime`` whose ``now`` is the scenario's clock: a question card's expiry (``parked_turn.with_expiry``)
    is read off the wall clock, which the journal's own times never are."""

    class _Datetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
            return clock.now().astimezone(tz)

    return _Datetime


async def run_durably(
    scenario: Scenario,
    live: Callable[[InMemoryJournal], LiveSinkPort] | None = None,
    *,
    crash_at: str | None = None,
    crash_on: int = 1,
    transports: list[ScriptedTransport] | None = None,
    after_backoff: Callable[[ScriptedTransport], None] | None = None,
    steer: Steer | None = None,
    history: list[dict[str, Any]] | None = None,
    executor: dict[str, Any] | None = None,
    transport: GoldenTransport | None = None,
    context_length: int = 128_000,
) -> tuple[list[JournalEvent], PipesHubContentStore]:
    """One durable run of ``scenario``. With ``crash_at`` the first worker dies
    at that failpoint, its lease lapses and a fresh assembler (another worker's
    process state) re-claims the run. ``history``: the session's legacy turns,
    which Node imports before the run (``session.imported``) and also sends in
    the run's input (``previousConversations``), as for a legacy session.
    ``executor``: pin a v3 spec carrying it (C-9); by default the v2 spec the goldens were recorded with.
    ``transport``/``context_length``: the model's transport and window (P4.8's long sessions)."""
    transport = transport or GoldenTransport()
    scenario.script(transport)
    if transports is not None:
        transports.append(transport)
    chat = scenario.route == "chat"

    async def model_factory(run: RunContext, services: object) -> ResolvedModel:
        if scenario.llm_unavailable:
            from app.harness.unpinned import resolve_model

            return await resolve_model(run, services)  # type: ignore[arg-type]
        return ResolvedModel(
            raw_llm=FakeChatModel(),
            provider="openai",
            is_multimodal=False,
            context_length=context_length,
            is_reasoning=False,
            port=ClassifiedModelPort(
                StreamingModelPort(TransportModel(transport), provider="openai", transport="scripted")
            ),
            transport=transport,
        )

    services = _chat_services() if chat else w.Services(agent=_golden_agent())
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock)
    spec = _chat_spec(scenario, executor) if chat else w.pinned_spec(tool_ids=[], kb=[], executor=executor)
    journal.admit(
        RUN,
        org_id=w.ORG,
        session_id="ses-1",
        spec=spec,
        input_text=scenario.query,
        imported=LegacyImport(history, len(history)) if history else None,
    )

    async def stop_button() -> None:
        journal.request_cancel(RUN)

    async def queue_steering() -> None:
        assert steer is not None
        if not any(e.type == "input.queued" for e in journal.events("ses-1")):
            _ = journal.queue_input(RUN, steer.text)

    golden_support._TOOL_HOOK["before_export"] = (
        queue_steering if steer is not None and steer.when == "export" else scenario.tool_hook(stop_button)
    )
    try:
        with ExitStack() as stack:
            for target, value in (
                ("app.agents.registry.toolset_registry.get_toolset_registry", scenario.toolset_registry()),
                ("app.services.artifact_registry.ArtifactRegistryService", golden_support._FakeArtifactRegistry),
                ("app.agents.actions.internal_tools.intrim_tools.uuid", _question_ids()),
                ("app.models.citation_source.uuid4", golden_support.live_ids()),
                ("app.agents.agent_loop.parked_turn.datetime", _on_clock(clock)),
                *golden_support.prefetch_patches(),
                ("app.api.routes.chatbot.get_llm_for_chat", golden_support.llm_for_chat(scenario)),
            ):
                stack.enter_context(patch(target, new=value))
            stack.enter_context(
                patch.dict(
                    "os.environ",
                    {"PIPESHUB_ENABLE_SKILLS": "false", "PIPESHUB_ENABLE_CODE_EXECUTION": "false", **scenario.env},
                )
            )
            bus = live(journal) if live is not None else None
            if steer is not None and steer.when == "first_delta":
                bus = _SteeringBus(journal, steer.text, bus)

            def driver() -> RunDriver:
                assembler = PipesHubRuntimeAssembler(services, model_factory=model_factory)
                return RunDriver(
                    journal=journal,
                    # The chat goldens' client sent no time zone.
                    assembler=_Attach(
                        assembler,
                        scenario.query,
                        scenario.chat_mode,
                        timezone=None if chat else "UTC",
                        previous=history,
                    ),
                    clock=clock,
                    live=bus,
                    sleep=_no_wait,
                )

            if crash_at is None and after_backoff is not None:
                claim = await journal.claim(RUN, worker_id="golden-worker")
                assert claim is not None
                _ = await driver().run_claimed(claim)
                after_backoff(transport)
                _ = clock.advance(3600)
                _ = journal.sweep()

            claim = await journal.claim(RUN, worker_id="golden-worker")
            assert claim is not None
            if crash_at is not None:
                arm = Arm(action=Raise(_Crash), trigger=Nth(crash_on))
                with armed({crash_at: arm}), contextlib.suppress(_Crash):
                    _ = await driver().run_claimed(claim)
                _ = clock.advance(claim.lease_ms / 1000 + 1)
                _ = journal.sweep()
                claim = await journal.claim(RUN, worker_id="golden-worker-2")
                assert claim is not None and claim.attempt == 2
            _ = await driver().run_claimed(claim)
    finally:
        golden_support._TOOL_HOOK["before_export"] = None
    content = PipesHubContentStore(journal.content)
    return journal.events("ses-1"), content


def test_tl1_durable_runs_get_the_legacy_turn_budget() -> None:
    """Node pins the legacy agent loop's turn limit (PA-4); the vector is what
    Node's resolver pins, so both languages agree with the legacy constant."""
    from pathlib import Path

    from app.agents.agent_loop.factory import _MAX_TURNS

    vector = json.loads(
        (Path(__file__).resolve().parents[4] / "contracts" / "harness" / "vectors" / "pinned-run-spec.json").read_text()
    )
    assert vector["pinned"]["spec"]["budgets"]["maxTurns"] == _MAX_TURNS
    assert w.pinned_spec(tool_ids=[]).budgets.max_turns == _MAX_TURNS


@pytest.mark.parametrize("scenario", DURABLE_SCENARIOS, ids=[s.name for s in DURABLE_SCENARIOS])
async def test_d5_durable_journal_matches_the_legacy_golden(scenario: Scenario) -> None:
    logging.getLogger("assembly.tests").setLevel(logging.WARNING)
    legacy = legacy_transcript((GOLDEN_DIR / f"{scenario.name}.sse").read_text(encoding="utf-8"))
    events, content = await run_durably(scenario)
    durable = await durable_transcript(events, content)
    assert durable.tools == legacy.tools
    assert durable.outcome == legacy.outcome
    assert durable.answer == legacy.answer


async def test_pr1_the_react_pre_run_runs_once_across_a_crash_and_a_reclaim() -> None:
    """The intent call is a model call: a worker that dies after the pre-run
    commit leaves it journaled, and the next worker reads it back (K-11)."""
    scenario = next(s for s in AGENT_SCENARIOS if s.chat_mode == "react")
    legacy = legacy_transcript((GOLDEN_DIR / f"{scenario.name}.sse").read_text(encoding="utf-8"))
    transports: list[ScriptedTransport] = []
    events, content = await run_durably(
        scenario, crash_at="worker.prerun.after_commit", transports=transports
    )
    assert [e.type for e in events].count("x.pipeshub.goal.presented") == 1
    goals = [e.data["slots"] for e in events if e.type == "loop.state_saved" and "pre_run.goal" in e.data["slots"]]
    assert len(goals) == 1
    durable = await durable_transcript(events, content)
    # Had the intent run again it would have eaten the first tool call's reply.
    assert durable.tools == legacy.tools
    assert durable.answer == legacy.answer
    assert transports[0]._index == len(transports[0]._script)


def _requests(transport: ScriptedTransport) -> list[dict[str, Any]]:
    # UXR-B1's allowed difference (``plan_tool``): durable react offers write_todos, today's loop never did.
    return [
        {
            "tools": [t.name for t in call["tools"] or [] if t.name != "write_todos"],
            "messages": [m.model_dump(mode="json", exclude_none=True) for m in call["messages"]],
        }
        for call in transport.calls
    ]


@pytest.mark.parametrize("scenario", DURABLE_SCENARIOS, ids=[s.name for s in DURABLE_SCENARIOS])
async def test_hk4_every_model_request_is_the_legacy_request(scenario: Scenario) -> None:
    """Request parity, call by call (the intent call included): the tools
    offered and the messages sent, after the goal replaces the input, the step
    footers and the PRE_MODEL shapers. The system prompt's parity is PR-2's
    (the two recordings run in different fixture environments)."""
    legacy: list[ScriptedTransport] = []
    _ = await golden_support.record_stream(scenario, legacy)
    durable: list[ScriptedTransport] = []
    _ = await run_durably(scenario, transports=durable)
    assert _requests(durable[0]) == _requests(legacy[0])
    assert legacy[0].calls or scenario.llm_unavailable, "the scenario calls the model"
    if scenario.route == "chat":
        # Both recordings run in the same world for chat: the whole prompt is
        # legacy's (mode addendum, custom instructions, prefetch context).
        assert [c["system"] for c in durable[0].calls] == [c["system"] for c in legacy[0].calls]
        from app.agents.chat_modes.policy import resolve_chat_mode_policy
        from app.agents.chat_modes.setup import with_mode_instructions

        addendum = with_mode_instructions(None, resolve_chat_mode_policy(scenario.chat_mode))
        assert all(addendum[:80] in c["system"] for c in durable[0].calls)


# A legacy conversation's earlier turns as Node sends them (`previousConversations`):
# an answer with its tool calls, and one whose tool result is an artifact.
LEGACY_HISTORY: list[dict[str, Any]] = [
    {"role": "user_query", "content": "Where is the Q2 report?"},
    {
        "role": "bot_response",
        "content": "It is in the finance folder.",
        "tool_results": [
            {
                "tool_id": "call-old-search",
                "tool_name": "goldendocs__search_docs",
                "args": {"query": "Q2 report"},
                "result": "Found: Q2 report (finance)",
                "result_summary": "Found: Q2 report (finance)",
                "status": "success",
            }
        ],
    },
    {"role": "user_query", "content": "Export it."},
    {
        "role": "bot_response",
        "content": "Exported.",
        "tool_results": [
            {
                "tool_id": "call-old-export",
                "tool_name": "goldendocs__export_report",
                "args": {"name": "q2"},
                "result": "rows: 3",
                "status": "success",
                "artifact_id": "art-q2",
            }
        ],
        "referenceData": [{"name": "Q2 report", "key": "q2"}],
    },
]


@pytest.mark.parametrize("scenario", DURABLE_SCENARIOS, ids=[s.name for s in DURABLE_SCENARIOS])
async def test_mg1b_with_an_imported_history_every_request_is_the_legacy_request(scenario: Scenario) -> None:
    """MG-1 (P1b T3): the first durable run in a legacy session. Node imports
    the conversation's turns (``session.imported``); every model request the
    run sends equals legacy's for the same history, call by call: the prior
    turns with their tool calls and artifact results, then the goal."""
    legacy: list[ScriptedTransport] = []
    _ = await golden_support.record_stream(scenario, legacy, history=LEGACY_HISTORY)
    durable: list[ScriptedTransport] = []
    events, _ = await run_durably(scenario, transports=durable, history=LEGACY_HISTORY)
    assert _requests(durable[0]) == _requests(legacy[0])
    if legacy[0].calls:
        first = [m.model_dump(mode="json", exclude_none=True) for m in legacy[0].calls[-1]["messages"]]
        assert "Where is the Q2 report?" in json.dumps(first), "legacy sent the history"
    (imported,) = [e for e in events if e.type == "session.imported"]
    assert imported.data["messages"] == len(LEGACY_HISTORY)
    assert [e.type for e in events][:2] == ["session.imported", "run.admitted"]


@dataclass
class _HistoryAttachments:
    """Storage for a history turn's attachment: its text and a citation ref in
    the request's mapper, as the resolver renders a document."""

    calls: list[list[str]]

    async def __call__(
        self,
        attachments: list[dict[str, Any]],
        blob_store: Any,  # noqa: ANN401
        org_id: str,
        ref_mapper: Any,  # noqa: ANN401
        vrmap: dict[str, Any],
        **_: Any,  # noqa: ANN401
    ) -> tuple[str, list[dict[str, Any]]]:
        assert blob_store is not None and org_id
        names = [str(a["recordName"]) for a in attachments]
        self.calls.append(names)
        refs = [ref_mapper.get_or_create_ref(f"https://records/{a['recordId']}/blocks/0") for a in attachments]
        vrmap.update({str(a["virtualRecordId"]): {"record_name": a["recordName"]} for a in attachments})
        return "\n".join(f"<record {n}> Citation ID: {r}" for n, r in zip(names, refs, strict=True)), []


HISTORY_WITH_ATTACHMENT: list[dict[str, Any]] = [
    {
        "role": "user_query",
        "content": "Summarize the attached policy.",
        "attachments": [
            {"recordId": "rec-policy", "recordName": "policy.pdf", "mimeType": "application/pdf", "virtualRecordId": "vr-policy"}
        ],
    },
    {"role": "bot_response", "content": "It grants 25 days of leave [source](ref1)."},
]


@pytest.mark.parametrize("scenario", DURABLE_SCENARIOS, ids=[s.name for s in DURABLE_SCENARIOS])
async def test_mg3f_a_legacy_attachment_reaches_the_durable_model_as_legacy_resolves_it(scenario: Scenario) -> None:
    """MG-3f: an attachment on an imported turn is resolved from storage by the
    resolver legacy uses, into the same message, with its citation ref in the
    run's mapper (the next ref continues after it)."""
    requests: list[list[dict[str, Any]]] = []
    resolved: list[list[list[str]]] = []
    for record in (golden_support.record_stream, run_durably):
        resolver = _HistoryAttachments([])
        transports: list[ScriptedTransport] = []
        with patch("app.agents.agent_loop.factory.resolve_history_attachments", new=resolver):
            if record is run_durably:
                _ = await run_durably(scenario, transports=transports, history=HISTORY_WITH_ATTACHMENT)
            else:
                _ = await golden_support.record_stream(scenario, transports, history=HISTORY_WITH_ATTACHMENT)
        resolved.append(resolver.calls)
        requests.append(_requests(transports[0]))
    legacy, durable = requests
    assert durable == legacy
    # Legacy builds (and seeds) the agent before it asks a clarification back;
    # the durable run asks at the pre-run gate and renders no history. No
    # request differs: the agent never calls the model.
    clarifies = scenario.name == "agent_pre_run_clarification"
    assert resolved[1] == ([] if clarifies else resolved[0])
    if resolved[1]:
        assert resolved[0] == [["policy.pdf"]]
        assert "<record policy.pdf> Citation ID: ref1" in json.dumps(legacy[-1]["messages"])


MG2B_POINTS = [
    ("worker.prerun.after_commit", 1),
    ("worker.model.after_response_commit", 1),
    # The search's outcome (its intent is the third commit).
    ("worker.commit.after_ack", 4),
]


@pytest.mark.parametrize(("failpoint", "nth"), MG2B_POINTS, ids=[f"{p}#{n}" for p, n in MG2B_POINTS])
async def test_mg2b_a_reclaimed_run_sends_the_imported_history_as_the_uncrashed_run(failpoint: str, nth: int) -> None:
    """MG-2: a worker dies after the claim; the next one renders the same
    imported history (attachment resolved again, its citation ref the same)
    and the journal still holds one import."""
    scenario = next(s for s in AGENT_SCENARIOS if s.name == "agent_quick_tool_call")
    history = HISTORY_WITH_ATTACHMENT + LEGACY_HISTORY
    runs: list[tuple[list[dict[str, Any]], list[JournalEvent]]] = []
    for crash_at in (None, failpoint):
        transports: list[ScriptedTransport] = []
        with patch("app.agents.agent_loop.factory.resolve_history_attachments", new=_HistoryAttachments([])):
            events, _ = await run_durably(
                scenario, crash_at=crash_at, crash_on=nth, transports=transports, history=history
            )
        runs.append((_requests(transports[0]), events))
    (clean, _), (crashed, events) = runs
    assert crashed == clean
    assert [e.type for e in events].count("session.imported") == 1
    assert [e.type for e in events].count("run.claimed") == 2


def _steered_during_export(t: ScriptedTransport) -> None:
    from app.agent_loop_lib.core.messages import ToolCall

    t.add_tool_call(ToolCall(id="call-export", name="goldendocs__export_report", arguments={"name": "q3"}))
    t.add_text_chunks(["The Q3 report is exported. ", "Q4 is not closed yet, so it is not in it."])


def _steered_after_answer(t: ScriptedTransport) -> None:
    t.add_text_chunks(["You get 25 days ", "of paid leave a year."])
    t.add_text_chunks(["Sick leave is separate: ", "10 days a year."])


# P1b T2: durable only (legacy has no steering). A message the user sends
# while the run works: during a tool, and while the answer streams.
STEERED_SCENARIOS: list[tuple[Scenario, Steer]] = [
    (
        Scenario("agent_steered_during_tool", "agent", "quick", "Export the Q3 report", _steered_during_export),
        Steer("Add Q4 too, if it is closed.", "export"),
    ),
    (
        Scenario("agent_steered_after_answer", "agent", "quick", "How much paid leave do I get?", _steered_after_answer),
        Steer("And sick leave?", "first_delta"),
    ),
]
STEERING = {scenario.name: steer for scenario, steer in STEERED_SCENARIOS}
STEERED_ANSWERS = {
    "agent_steered_during_tool": "The Q3 report is exported. Q4 is not closed yet, so it is not in it.",
    "agent_steered_after_answer": "Sick leave is separate: 10 days a year.",
}


@pytest.mark.parametrize(("scenario", "steer"), STEERED_SCENARIOS, ids=[s.name for s, _ in STEERED_SCENARIOS])
async def test_st2_st3_the_next_model_call_sees_the_steering_message_at_its_tail(
    scenario: Scenario, steer: Steer
) -> None:
    from app.agent_loop_lib.core.messages import UserMessage

    transports: list[ScriptedTransport] = []
    events, content = await run_durably(scenario, transports=transports, steer=steer)
    types = [e.type for e in events]
    assert types.count("input.queued") == types.count("input.consumed") == 1
    assert types.count("run.completed") == 1
    (queued,) = [e for e in events if e.type == "input.queued"]
    assert queued.writer == "node" and queued.data["messageSeq"] == 1
    consumed_at = types.index("input.consumed")
    # Before the model call that reads it; after the tool's outcome, or the answer it followed up.
    assert types.index("turn.started", consumed_at) > consumed_at
    assert "tool.outcome" in types[:consumed_at] or scenario.name.endswith("after_answer")
    calls = transports[0].calls
    assert len(calls) == 2
    first, second = (call["messages"] for call in calls)
    assert all(m.content != steer.text for m in first if isinstance(m, UserMessage))
    assert isinstance(second[-1], UserMessage) and second[-1].content == steer.text
    durable = await durable_transcript(events, content)
    assert (durable.outcome, durable.answer) == ("answered", STEERED_ANSWERS[scenario.name])


# A crash before the model's response commit re-samples it (the kernel test covers
# that window with a model that answers twice); here the script answers once.
@pytest.mark.parametrize("failpoint", ["worker.input.before_consume_commit", "worker.input.after_consume_commit"])
async def test_st4_a_steered_run_crashed_around_the_consumption_answers_as_the_uncrashed_one(failpoint: str) -> None:
    scenario, steer = STEERED_SCENARIOS[0]
    clean: list[ScriptedTransport] = []
    events, content = await run_durably(scenario, transports=clean, steer=steer)
    expected = await durable_transcript(events, content)
    crashed: list[ScriptedTransport] = []
    events, content = await run_durably(scenario, transports=crashed, steer=steer, crash_at=failpoint)
    assert [e.type for e in events].count("input.consumed") == 1
    assert await durable_transcript(events, content) == expected
    last = crashed[0].calls[-1]["messages"]
    assert [m.content for m in last].count(steer.text) == 1


def _planned(t: ScriptedTransport) -> None:
    from app.agent_loop_lib.core.messages import ToolCall

    # react runs the intent briefing call before the agent's first turn.
    t.add_text("Find the paid leave policy and summarize it.")
    plan = [
        {"content": "Find the leave policy", "status": "in_progress", "tools": ["goldendocs__search_docs"]},
        {"content": "Summarize it", "status": "pending"},
    ]
    t.add_tool_call(ToolCall(id="call-plan-1", name="write_todos", arguments={"todos": plan}))
    t.add_tool_call(ToolCall(id="call-search", name="goldendocs__search_docs", arguments={"query": "paid leave"}))
    done = [{**plan[0], "status": "completed"}, {**plan[1], "status": "in_progress"}]
    t.add_tool_call(ToolCall(id="call-plan-2", name="write_todos", arguments={"todos": done}))
    t.add_text_chunks(["You get 25 days ", "of paid leave a year."])


# UXR-B1: durable only (legacy has no plan on its stream). A react run that plans, works under its steps, answers.
PLANNED_SCENARIOS = [
    Scenario("agent_react_planned", "agent", "react", "Summarize the leave policy", _planned),
]


async def test_b1_21_a_planned_react_run_journals_its_plan_around_the_work() -> None:
    events, content = await run_durably(PLANNED_SCENARIOS[0])
    tasks = [(e.type, e.data["taskId"], e.data["status"], e.critical) for e in events if e.type.startswith("task.")]
    assert tasks == [
        ("task.created", "task-1", "in_progress", False),
        ("task.created", "task-2", "pending", False),
        ("task.updated", "task-1", "completed", False),
        ("task.updated", "task-2", "in_progress", False),
    ]
    types = [e.type for e in events]
    search = next(i for i, e in enumerate(events) if e.type == "tool.outcome" and e.data["invocationId"] == "t2.0")
    assert types.index("task.created") < search < types.index("task.updated")
    durable = await durable_transcript(events, content)
    assert (durable.outcome, durable.answer) == ("answered", "You get 25 days of paid leave a year.")


def _empty_twice(t: ScriptedTransport) -> None:
    t.add_text("")
    t.add_text("")
    t.add_text("Here is the answer after two nudges.")


def _truncated(t: ScriptedTransport) -> None:
    t.add_truncated("The leave policy gives every full-time employee twenty")
    t.add_text("-five days of paid leave a year.")


LOOP_HOOK_SCENARIOS = [
    Scenario("agent_empty_response_nudged", "agent", "quick", "How much paid leave do I get?", _empty_twice),
    Scenario("agent_truncated_response_continued", "agent", "quick", "How much paid leave do I get?", _truncated),
]


@pytest.mark.parametrize("scenario", LOOP_HOOK_SCENARIOS, ids=[s.name for s in LOOP_HOOK_SCENARIOS])
async def test_hk3_post_model_recovery_continues_the_run_as_legacy_does(scenario: Scenario) -> None:
    """The completion gate and truncation recovery: the same recovery message
    reaches the next request, and the run answers as legacy answers."""
    legacy: list[ScriptedTransport] = []
    sse = await golden_support.record_stream(scenario, legacy)
    durable: list[ScriptedTransport] = []
    events, content = await run_durably(scenario, transports=durable)
    assert _requests(durable[0]) == _requests(legacy[0])
    assert len(durable[0].calls) == len(scenario_calls := legacy[0].calls) and len(scenario_calls) > 1
    assert _durable_answer(events) == _legacy_answer(sse)
    del content


def _legacy_answer(sse: str) -> str:
    """The legacy stream's final answer: its last final STATE_SNAPSHOT."""
    answer = ""
    for block in sse.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                frame = json.loads(line[5:].strip())
                snapshot = frame.get("snapshot") or {}
                if frame.get("type") == "STATE_SNAPSHOT" and snapshot.get("final"):
                    answer = str(snapshot.get("answer") or "")
    return answer


def _durable_answer(events: list[JournalEvent]) -> str:
    finalized = [e for e in events if e.type == "x.pipeshub.answer.finalized"]
    return str(finalized[-1].data["answer"]) if finalized else ""


def _final_snapshot(sse: str) -> dict[str, Any]:
    final: dict[str, Any] = {}
    for block in sse.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                frame = json.loads(line[5:].strip())
                if frame.get("type") == "STATE_SNAPSHOT" and (frame.get("snapshot") or {}).get("final"):
                    final = frame["snapshot"]
    return final


def _questions(tool_data: Any) -> list[tuple[str, list[str]]]:  # noqa: ANN401
    return [
        (q["question"], [o["label"] for o in q["options"]])
        for q in tool_data["questions"]
    ]


async def test_pr5_a_fatally_ambiguous_request_is_asked_back_without_running_the_agent() -> None:
    scenario = next(s for s in AGENT_SCENARIOS if s.name == "agent_pre_run_clarification")
    legacy: list[ScriptedTransport] = []
    sse = await golden_support.record_stream(scenario, legacy)
    durable: list[ScriptedTransport] = []
    events, _ = await run_durably(scenario, transports=durable)

    assert len(durable[0].calls) == len(legacy[0].calls) == 1, "only the intent call"
    expected = _final_snapshot(sse)
    [finalized] = [e.data for e in events if e.type == "x.pipeshub.answer.finalized"]
    assert finalized["outcome"] == "needs_input"
    for key in ("answer", "answerMatchType", "confidence"):
        assert finalized[key] == expected[key], key
    [asked] = [e.data for e in events if e.type == "x.pipeshub.question.asked"]
    legacy_card = next(
        json.loads(line[5:].strip())["value"]["toolData"]
        for block in sse.split("\n\n")
        for line in block.splitlines()
        if line.startswith("data:") and '"ask_user_question"' in line
    )
    assert _questions(asked["toolData"]) == _questions(legacy_card)
    assert [e.type for e in events][-1] == "run.completed"
    assert not [e for e in events if e.type == "model.request"]


async def test_pc1_a_rate_limited_chat_retries_where_legacy_shows_the_error() -> None:
    """`chat_llm_error` is a 429. Legacy ends the turn with the rate-limit
    error answer; a durable run yields for the backoff and asks the model again
    (L-18, REPORT 6.17), so it never shows that error: once the provider
    answers, the run ends with the answer the chat golden shows."""
    from tests.contracts.golden_support import normalize_sse
    from tests.contracts.scenarios import _chat_with_citations

    scenario = next(s for s in SCENARIOS if s.name == "chat_llm_error")
    answered = next(s for s in SCENARIOS if s.name == "chat_answer_with_citations")
    events, content = await run_durably(scenario, after_backoff=_chat_with_citations)
    types = [e.type for e in events]
    [yielded] = [e.data for e in events if e.type == "run.yielded"]
    assert yielded["reason"] == "llm_backoff"
    assert "run.failed" not in types and types[-1] == "run.completed"
    [finalized] = [e.data for e in events if e.type == "x.pipeshub.answer.finalized"]
    assert "answerMatchType" not in finalized, "never the error answer"
    legacy = legacy_transcript(normalize_sse((GOLDEN_DIR / f"{answered.name}.sse").read_text(encoding="utf-8")))
    assert (await durable_transcript(events, content)).answer == legacy.answer
    assert len(finalized["citations"]) == 2


async def test_c1_a_truncated_reply_and_its_continuation_are_one_part() -> None:
    """PA-16: legacy's collector joins a text-only reply that continues one cut
    off at the output-token limit into the cut-off part; the durable
    transcript keeps the same parts (one), and flags the cut-off response."""
    scenario = next(s for s in LOOP_HOOK_SCENARIOS if s.name == "agent_truncated_response_continued")
    legacy = _final_snapshot(await golden_support.record_stream(scenario))
    events, _ = await run_durably(scenario)
    [finalized] = [e.data for e in events if e.type == "x.pipeshub.answer.finalized"]
    assert finalized["partTexts"] == [p["content"] for p in legacy["parts"] if p["type"] == "text"]
    assert len(finalized["partTexts"]) == 1
    presented = [e.data for e in events if e.type == "x.pipeshub.message.presented"]
    assert [bool(p.get("truncated")) for p in presented] == [True, False]
