"""K-L GD-1..GD-6: deep mode, legacy and durable, on the same scripted model.

Legacy runs ``OrchestratorLoop`` with its sub-agents as in-process tasks; the
durable run is the orchestrator on the kernel's loop director with each
sub-agent a durable child run (``RunDriver`` over the in-memory journal, the
real assembler on both sides). Every agent's requests (tools and messages)
and the final answer must be legacy's; what differs by design is asserted as
such and listed in the K-L doc.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import re
from contextlib import ExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.harness.adapters.content_store import PipesHubContentStore
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.model import ClassifiedModelPort, ResolvedModel
from harness_core.contracts.generated.common import ContentRef
from harness_core.driver import RunDriver
from harness_core.failpoints import Arm, Nth, Raise
from harness_core.kernel.durable.adapters import StreamingModelPort
from harness_core.kernel.durable.transcript import decode_assistant, decode_result
from harness_core.models.transport_model import TransportModel
from harness_core.testing import ManualClock, armed
from tests.contracts import golden_support
from tests.contracts.deep_scenarios import (
    DEEP_SCENARIOS,
    INTENT,
    ORCHESTRATOR,
    DeepScenario,
    DeepScript,
    DeepTransport,
)
from tests.contracts.golden_support import (
    GOLDEN_DIR,
    Scenario,
    normalize_sse,
    update_goldens,
)
from tests.contracts.test_durable_goldens import (
    _golden_agent,  # pyright: ignore[reportPrivateUsage]
    _question_ids,  # pyright: ignore[reportPrivateUsage]
    )
from tests.unit.agents.adapter.conftest import FakeChatModel
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.harness.assembly.context import RunContext
    from harness_core.journal import JournalEvent
    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

RUN = "run-deep"
SESSION = "ses-1"
TERMINAL = ("completed", "failed", "cancelled", "quarantined")


def _scenario(deep: DeepScenario) -> Scenario:
    return Scenario(deep.name, "agent", "deep", deep.query, lambda _t: None, toolsets=deep.toolsets)


async def record_legacy(deep: DeepScenario) -> tuple[str, DeepScript]:
    script = deep.script()
    sse = await golden_support.record_stream(_scenario(deep), transport=DeepTransport(script))
    return sse, script


class _Crash(BaseException):
    """A worker dying at a failpoint: not an Exception, so no handler runs."""


class _Attach:
    """In-memory claims carry no run snapshot: attach the admitted one (a child's
    input is its goal, in its parent's mode)."""

    def __init__(self, inner: PipesHubRuntimeAssembler, budget: Callable[[Any], Awaitable[Any]] | None = None) -> None:
        self._inner = inner
        self._budget = budget

    async def build(self, claim: Any) -> Any:  # noqa: ANN401
        node = w.claim(claim.spec, query=claim.input_text, run_id=claim.run_id, chat_mode="deep", timezone="UTC")
        values = {f.name: getattr(claim, f.name) for f in dataclasses.fields(claim)}
        accounts = None if self._budget is None else await self._budget(claim)
        return await self._inner.build(type(node)(**values, run=node.run, budget=accounts))


@dataclass
class DurableDeep:
    journal: Any
    content: PipesHubContentStore
    script: DeepScript

    def events(self, stream: str = SESSION) -> list[JournalEvent]:
        return self.journal.events(stream)

    def children(self) -> list[str]:
        return list(self.journal.children_of(RUN))


def deep_spec(max_children: int = 16) -> Any:  # noqa: ANN401
    spec = w.pinned_spec(tool_ids=[], kb=[])
    return spec.model_copy(update={"budgets": spec.budgets.model_copy(update={"max_children": max_children})})


async def run_deep_durably(
    deep: DeepScenario,
    *,
    crash: tuple[str, str, int] | tuple[str, str, int, int] | None = None,
    cancel_parent_after_spawn: bool = False,
    max_children: int = 16,
    max_attempts: int | None = None,
    spec: Any = None,  # noqa: ANN401
    workspaces: Any = None,  # noqa: ANN401
    child_workspaces: Any = None,  # noqa: ANN401
    child_ran: Callable[[Any, str], Awaitable[None]] | None = None,
    transport_wrapper: Callable[[Any], Any] | None = None,
    budget: Callable[[Any], Awaitable[Any]] | None = None,
) -> DurableDeep:
    """The parent until it waits, then each queued child in admission order,
    then the parent again, until it ends. ``crash``: (run: "parent"|"child",
    failpoint, nth[, times]) kills that many attempts of that kind of run there;
    each lease lapses. P6.4: ``workspaces`` forks a child's workspace at
    admission, ``child_workspaces`` merges it at the join, ``child_ran(journal,
    child)`` runs after each child's attempt (what its engine saved).
    ``budget``: the ledger accounts a claim's run charges (P6.5), given the
    claim; ``transport_wrapper`` wraps the scripted transport (usage)."""
    script = deep.script()
    transport = DeepTransport(script) if transport_wrapper is None else transport_wrapper(DeepTransport(script))

    async def model_factory(run: RunContext, services: object) -> ResolvedModel:
        return ResolvedModel(
            raw_llm=FakeChatModel(),
            provider="openai",
            is_multimodal=False,
            context_length=128_000,
            is_reasoning=False,
            port=ClassifiedModelPort(
                StreamingModelPort(TransportModel(transport), provider="openai", transport="scripted")
            ),
            transport=transport,
            transport_name="scripted",
        )

    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, workspaces=workspaces)
    journal.admit(
        RUN, org_id=w.ORG, session_id=SESSION, spec=spec or deep_spec(max_children), input_text=deep.query
    )
    services = w.Services(agent=_golden_agent())
    with ExitStack() as stack:
        for target, value in (
            ("app.agents.registry.toolset_registry.get_toolset_registry", _scenario(deep).toolset_registry()),
            ("app.services.artifact_registry.ArtifactRegistryService", golden_support._FakeArtifactRegistry),  # pyright: ignore[reportPrivateUsage]
            ("app.agents.actions.internal_tools.intrim_tools.uuid", _question_ids()),
            ("app.models.citation_source.uuid4", golden_support.live_ids()),
            *golden_support.prefetch_patches(),
        ):
            stack.enter_context(patch(target, new=value))
        stack.enter_context(
            patch.dict("os.environ", {"PIPESHUB_ENABLE_SKILLS": "false", "PIPESHUB_ENABLE_CODE_EXECUTION": "false"})
        )

        def driver() -> RunDriver:
            kwargs: dict[str, Any] = {} if max_attempts is None else {"max_attempts": max_attempts}
            return RunDriver(
                journal=journal,
                assembler=_Attach(PipesHubRuntimeAssembler(services, model_factory=model_factory), budget),
                clock=clock,
                children=journal,
                child_workspaces=child_workspaces,
                **kwargs,
            )

        crashes = 0 if crash is None else (crash[3] if len(crash) > 3 else 1)  # type: ignore[misc]
        for _ in range(60):
            if journal.status(RUN) in TERMINAL and all(
                journal.status(c) in TERMINAL for c in journal.children_of(RUN)
            ):
                break
            queued = [r for r in (RUN, *journal.children_of(RUN)) if journal.status(r) == "queued"]
            if not queued:
                _ = clock.advance(31)
                _ = journal.sweep()
                continue
            run_id = queued[0]
            claim = await journal.claim(run_id, worker_id="w")
            assert claim is not None
            who = "parent" if run_id == RUN else "child"
            if crashes > 0 and crash is not None and crash[0] == who:
                crashes -= 1
                arm = Arm(action=Raise(_Crash), trigger=Nth(crash[2]))
                with armed({crash[1]: arm}), contextlib.suppress(_Crash):
                    _ = await driver().run_claimed(claim)
                _ = clock.advance(claim.lease_ms / 1000 + 1)
                _ = journal.sweep()
                continue
            _ = await driver().run_claimed(claim)
            if child_ran is not None and run_id != RUN:
                await child_ran(journal, run_id)
            if cancel_parent_after_spawn and run_id == RUN and journal.children_of(RUN):
                journal.request_cancel(RUN)
                cancel_parent_after_spawn = False
        else:
            raise AssertionError("the deep run did not settle")
    return DurableDeep(journal, PipesHubContentStore(journal.content), script)


def requests(agent: ScriptedTransport) -> list[dict[str, Any]]:
    """Each request's tools and messages. A programmatic dispatch's call ids end
    in a random suffix in legacy and a derived one here (a re-claim must agree)."""
    shown = [
        {
            "tools": [t.name for t in call["tools"] or []],
            "messages": [m.model_dump(mode="json", exclude_none=True) for m in call["messages"]],
        }
        for call in agent.calls
    ]
    return json.loads(_PROGRAMMATIC_ID.sub(r"\1_<id>", json.dumps(shown)))


_PROGRAMMATIC_ID = re.compile(r"(programmatic_spawn_[A-Za-z0-9_.:-]+?)_[0-9a-f]{8}\b")


async def durable_answer(run: DurableDeep) -> tuple[str, str]:
    answer, outcome = "", "failed"
    for event in run.events():
        if event.run_id != RUN:
            continue
        if event.type == "message.completed" and event.data["role"] == "assistant" and event.data["isFinal"]:
            answer = (await run.content.get(ContentRef.model_validate(event.data["textRef"]))).decode()
        elif event.type == "run.completed":
            outcome = str(event.data["outcome"])
    return answer, outcome


# ---- GD-1..GD-5: request parity, agent by agent, and the answer ----


def _tool_message(requests_: list[dict[str, Any]], call_id: str) -> list[dict[str, Any]]:
    return [
        m for r in requests_ for m in r["messages"] if m.get("role") == "tool" and m.get("tool_call_id") == call_id
    ]


def _own_child_denied(legacy: dict[str, list[dict[str, Any]]]) -> None:
    """GD-3, a legacy defect durable runs do not have: legacy pre-launches both
    spawns of the turn, then runs their calls one by one through the phase gate;
    the second call's own child has already succeeded, so the gate refuses it as a
    re-dispatch and the orchestrator never reads that child's answer."""
    shown = _tool_message(legacy[ORCHESTRATOR], "call-ticket")
    assert shown and all(m["content"].startswith("task_id 'ticket' already completed") for m in shown)
    for message in shown:
        message["content"] = json.dumps({"output": "T-42 is resolved.", "success": True})
        message["is_error"] = False


def _failed_child_reads_null(legacy: dict[str, list[dict[str, Any]]]) -> None:
    """GD-5: legacy's failed child has no output, so its call reads ``null``; a
    durable child's failure reads Node's error type."""
    (shown,) = _tool_message(legacy[ORCHESTRATOR], "programmatic_spawn_ticket_<id>")
    assert (shown["content"], shown["is_error"]) == ("null", True)
    shown["content"] = "auth_error"


def _sub_agent_offered_coordination(legacy: dict[str, list[dict[str, Any]]]) -> None:
    """GD-2a (ADR-RUN-04), a legacy defect durable runs do not have: a plan step that names no tools hands its child
    ``tools: []``, the whole shared registry, so legacy's child is offered the orchestrator's coordination tools. A
    durable sub-agent holds none: it cannot start sub-agents."""
    from app.harness.assembly.deep import COORDINATION_TOOLS

    offered = [r for r in legacy["summary"] if COORDINATION_TOOLS & set(r["tools"])]
    assert offered, "legacy's tool-less step no longer offers its child the coordination tools"
    for request in offered:
        request["tools"] = [t for t in request["tools"] if t not in COORDINATION_TOOLS]


def coordinator_may_ask(legacy: dict[str, list[dict[str, Any]]]) -> None:
    """ADR-RUN-04 D1 amended (R36-COVER), every scenario: the durable coordinator also holds the person's question,
    so a sub-agent's escalation can be put to the person; legacy's (classic chat, no ask-back) does not."""
    from app.agents.agent_loop.loops.orchestrator import COORDINATOR_QUESTION_TOOLS

    for request in legacy[ORCHESTRATOR]:
        assert not set(COORDINATOR_QUESTION_TOOLS) & set(request["tools"]), "legacy's coordinator now asks"
        request["tools"] = [*request["tools"], *COORDINATOR_QUESTION_TOOLS]


# Differences a durable deep run has by design (each asserted on legacy first).
KNOWN_DIFFERENCES = {
    "deep_llm_dispatch": _own_child_denied,
    "deep_child_fails": _failed_child_reads_null,
    "deep_dependent_steps": _sub_agent_offered_coordination,
}


@pytest.mark.parametrize("deep", DEEP_SCENARIOS, ids=[d.name for d in DEEP_SCENARIOS])
async def test_gd_every_agents_requests_and_the_answer_are_legacys(deep: DeepScenario) -> None:
    sse, legacy = await record_legacy(deep)
    durable = await run_deep_durably(deep)
    assert sorted(durable.script.agents) == sorted(legacy.agents)
    expected = {key: requests(agent) for key, agent in legacy.agents.items()}
    if deep.name in KNOWN_DIFFERENCES:
        KNOWN_DIFFERENCES[deep.name](expected)
    coordinator_may_ask(expected)
    for key, agent in legacy.agents.items():
        assert agent.calls or agent.structured_calls, f"{key} was never asked"
        assert requests(durable.script.agents[key]) == expected[key], key
        assert [c["messages"] for c in durable.script.agents[key].structured_calls] == [
            c["messages"] for c in agent.structured_calls
        ], key
    answer, outcome = await durable_answer(durable)
    assert (answer, outcome) == legacy_ending(sse)


def legacy_ending(sse: str) -> tuple[str, str]:
    """The answer the root RUN_FINISHED carries (deep mode's text may arrive
    unstreamed); a sub-agent's RUN_ERROR (it names its parent) ends nothing."""
    answer, outcome = "", "failed"
    for block in sse.split("\n\n"):
        data = next((line[5:].strip() for line in block.splitlines() if line.startswith("data:")), None)
        if not data:
            continue
        frame = json.loads(data)
        if frame.get("parentRunId"):
            continue
        if frame.get("type") == "RUN_FINISHED" and frame.get("result") is not None:
            answer, outcome = str(frame["result"].get("answer") or ""), "answered"
        elif frame.get("type") == "RUN_ERROR":
            outcome = "failed"
    return answer, outcome


def test_gd_scenarios_route_every_agent() -> None:
    for deep in DEEP_SCENARIOS:
        assert INTENT in deep.script().agents and ORCHESTRATOR in deep.script().agents


# ---- GD-6: the legacy SSE goldens of the deep scenarios ----


@pytest.mark.parametrize("deep", DEEP_SCENARIOS, ids=[d.name for d in DEEP_SCENARIOS])
async def test_gd6_legacy_deep_stream_matches_its_golden(deep: DeepScenario) -> None:
    sse, _ = await record_legacy(deep)
    stream = normalize_sse(sse)
    path = GOLDEN_DIR / "deep" / f"{deep.name}.sse"
    if update_goldens():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(stream, encoding="utf-8")
        return
    assert path.exists(), f"missing golden {path}; record it with UPDATE_GOLDENS=1"
    assert stream == path.read_text(encoding="utf-8")


async def _tool_results(run: DurableDeep, stream: str) -> list[tuple[str, str, bool]]:
    calls: dict[str, str] = {}
    out: list[tuple[str, str, bool]] = []
    for event in run.journal.events(stream):
        if event.type == "model.response":
            message = decode_assistant(await run.content.get(ContentRef.model_validate(event.data["messageRef"])))
            for i, call in enumerate(message.tool_calls or []):
                calls[f"{event.data.get('turn', '')}{i}"] = call.name
        if event.type == "tool.outcome":
            content, is_error = decode_result(await run.content.get(ContentRef.model_validate(event.data["resultRef"])))
            out.append((str(event.data["invocationId"]), json.dumps(content), is_error))
    return out
