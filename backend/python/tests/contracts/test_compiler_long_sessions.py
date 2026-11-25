"""P4.8 long sessions (LS-1..LS-4): a PipesHub durable run whose prompt passes 50% and then
80% of the window, through the real assembler and kernel, so the context compiler's ladder
(``context.cleared``, ``context.compacted``) fires on real measurements, and the same run
with the compiler off reduces history only through the journaled ladder (the legacy shapers that cut history, L2-L8,
are legacy-only: QAFIX4 B2, LONGTASK-F4).

The model is scripted; its reported usage is the size of the request it was sent (about
four characters a token), and it answers the compiler's summarising call with a summary
that names no id at all, so what survives a compaction is what the compiler keeps, not
what a good summariser would have kept.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.responses import ModelResponse, TokenUsage
from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from harness_core.context.compaction import SUMMARY_INSTRUCTION
from harness_core.context.compiler import assert_paired
from tests.contracts.golden_support import GoldenTransport, Scenario
from tests.contracts.test_durable_goldens import Steer, run_durably
from tests.unit.agents.adapter.support.scripted_transport import ScriptedStep

if TYPE_CHECKING:
    import pytest

    from app.agent_loop_lib.core.tool_schema import ToolSchema
    from harness_core.journal import JournalEvent

# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

FLAG = "HARNESS_CONTEXT_COMPILER"
WINDOW = 128_000
QUERY = "Build the quarterly review from the regional reports."
STEERING = "Also include the Q4 numbers for the review."
NOTES_CHARS = 32_000  # what the model writes each turn, about 8k tokens
REPORT_CHARS = 24_000  # each report, about 6k tokens: an artifact (registration above 4k)
# Fourteen reads reach the compact rung: since BUDGETEND the clear rung keeps the tail to a quarter of the window,
# so eleven no longer pass 80%.
READS = 14
SUMMARY = "Summary: the user wants a quarterly review; several regional reports were read and noted."
_WORDS = (
    "revenue margin region forecast pipeline churn headcount bookings quota renewal backlog "
    "variance budget hiring capacity incident roadmap pricing segment cohort retention"
).split()


def prose(seed: str, chars: int) -> str:
    rng = random.Random(seed)
    words: list[str] = []
    size = 0
    while size < chars:
        word = rng.choice(_WORDS)
        words.append(word)
        size += len(word) + 1
    return f"[{seed}] " + " ".join(words)[:chars]


class GoldenReports:
    """A regional report: long enough that legacy registers it as an artifact."""

    @tool(
        path="/tools/goldenreports/read_report",
        short_description="Read a regional report",
        description="Read one regional report in full.",
        parameters=[ToolParameter(name="region", type=ParameterType.STRING, description="Region", required=True)],
        tags=[Tag(key="effect", value="read")],
    )
    async def read_report(self, region: str) -> str:
        return prose(f"report-{region}", REPORT_CHARS)


class MeasuringTransport(GoldenTransport):
    """Usage is the request's size; the compiler's summarising call is answered here."""

    def __init__(self) -> None:
        super().__init__()
        self.summaries = 0

    async def complete(
        self,
        messages: list[Any],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        *,
        tool_choice: object = None,
    ) -> ModelResponse:
        size = len(system or "") + sum(len(json.dumps(m.model_dump(mode="json"))) for m in messages)
        size += sum(len(json.dumps(t.model_dump(mode="json"))) for t in tools or ())
        usage = TokenUsage(input_tokens=size // 4, output_tokens=200)
        first = messages[0].content if messages else ""
        if isinstance(first, str) and first.startswith(SUMMARY_INSTRUCTION.split("{", 1)[0][:60]):
            self.summaries += 1
            return ModelResponse(message=AssistantMessage(content=SUMMARY), usage=usage, model="scripted-model")
        response = await super().complete(
            messages, tools=tools, system=system, model=model,
            thinking_budget=thinking_budget, effort=effort, system_blocks=system_blocks,
            tool_choice=tool_choice,  # type: ignore[arg-type]
        )
        return response.model_copy(update={"usage": usage})


def _long_session(t: GoldenTransport) -> None:
    for index in range(READS):
        notes = prose(f"notes-{index}", NOTES_CHARS)
        if index == 2:
            call = ToolCall(id="call-export", name="goldendocs__export_report", arguments={"name": "q3"})
        else:
            call = ToolCall(id=f"call-read-{index}", name="goldenreports__read_report", arguments={"region": f"r{index}"})
        t._script.append(
            ScriptedStep(message=AssistantMessage(content=[TextPart(text=notes)], tool_calls=[call]))
        )
    t.add_text("The quarterly review: revenue grew in every region; Q4 numbers are included.")


SCENARIO = Scenario(
    "agent_long_session",
    "agent",
    "quick",
    QUERY,
    _long_session,
    toolsets={"goldenreports": {"class": GoldenReports, "isInternal": True, "provider": "platform", "essential": False, "description": "Reports"}},
)


async def _register(self: object, *, name: str, content: bytes, conversation_id: str | None = None, **_: Any) -> Any:  # noqa: ANN401
    """The fake registry stores a large tool result as an artifact (legacy's registration path)."""
    from app.services.artifact_registry.models import ArtifactMetadata, ArtifactType

    artifact_id = f"art-{hashlib.sha256(content).hexdigest()[:10]}"
    return ArtifactMetadata(
        artifact_id=artifact_id, org_id="org-1", conversation_id=conversation_id, name=name,
        logical_name=name, artifact_type=ArtifactType.TOOL_RESULT, mime_type="application/json",
        version=1, size_bytes=len(content),
    )


async def _run(monkeypatch: pytest.MonkeyPatch, flag: str) -> tuple[list[JournalEvent], MeasuringTransport]:
    from tests.contracts import golden_support

    monkeypatch.setattr(golden_support._FakeArtifactRegistry, "register", _register, raising=False)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setenv(FLAG, flag)
    transport = MeasuringTransport()
    events, _ = await run_durably(
        SCENARIO, transport=transport, context_length=WINDOW, steer=Steer(STEERING, "export")
    )
    return events, transport


def _text(message: Any) -> str:  # noqa: ANN401
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(p.text for p in content if isinstance(p, TextPart))


def _artifacts(message: Any) -> list[str]:  # noqa: ANN401
    meta = getattr(message, "artifact_meta", None)
    return [meta.artifact_id] if meta is not None else []


# ---- LS-1, LS-2: the compiler's ladder fires, and what must survive does ----


async def test_ls1_ls2_the_ladder_fires_and_the_run_keeps_what_it_needs(monkeypatch: pytest.MonkeyPatch) -> None:
    events, transport = await _run(monkeypatch, "true")
    types = [e.type for e in events]
    assert types.count("run.completed") == 1
    assert "context.cleared" in types and "context.compacted" in types
    assert types.index("context.cleared") < types.index("context.compacted")
    assert transport.summaries == types.count("context.compacted")

    calls = transport.calls
    system = calls[0]["system"]
    seen: set[str] = set()
    steered = False
    reduced = {"cleared": 0, "summarised": 0}
    for index, call in enumerate(calls):
        messages = call["messages"]
        # No tail: the run's context and the deadline warning are notes, history (P4.11).
        history = messages
        text = "\n".join(_text(m) for m in messages)
        assert_paired(history)
        # Pinned: the system band is the same bytes on every turn of the run.
        assert call["system"] == system, f"call {index}: system band changed"
        # The goal: the run's own input, verbatim, as a user message (re-injected after a summary).
        assert any(isinstance(m, UserMessage) and not m.injected and _text(m) == QUERY for m in history), index
        # The latest user input, once consumed: verbatim.
        steered = steered or STEERING in text
        if steered:
            assert any(isinstance(m, UserMessage) and _text(m) == STEERING for m in history), index
        # The newest tool calls and their results (what the model has not acted on yet): verbatim.
        last = next((m for m in reversed(history) if isinstance(m, AssistantMessage) and m.tool_calls), None)
        if last is not None:
            results = {m.tool_call_id: m for m in history if isinstance(m, ToolMessage)}
            for call_ in last.tool_calls or []:
                assert call_.id in results and "cleared" not in _text(results[call_.id]), (index, call_.id)
        # Every artifact the run has seen is still named.
        # Every artifact the run has seen is still there: its result whole, or named (a reference, an index).
        whole = {a for m in history for a in _artifacts(m)}
        for artifact in seen:
            assert artifact in whole or artifact in text, f"call {index}: artifact {artifact} lost"
        for m in history:
            seen.update(_artifacts(m))
        reduced["cleared"] += "cleared from the context" in text or "[artifact:" in text
        reduced["summarised"] += SUMMARY in text
        # The run's context (the time) reaches every request, a compaction included.
        assert "Current time" in text, f"call {index}: the run's context was lost"
    assert seen, "the reports were registered as artifacts"
    assert reduced["cleared"] and reduced["summarised"]


# ---- LS-4: what the legacy shapers do on the same session ----

SHAPER_MARKERS = {
    "L1 budget reduction": re.compile(r"by budget_reduction\]"),
    "L2/L3 artifact reference": re.compile(r"^\[artifact:", re.M),
    "L3 tool-result reference": re.compile(r"^tool: .*\n(args: .*\n)?tool_call_id: ", re.M),
    "L4 loop compaction": re.compile(r"\[Loop compaction:"),
    "L6 deterministic compact": re.compile(r"^\[compacted\] |^\[tool_call_id: ", re.M),
    "L8 synthesis guard": re.compile(r"by synthesis_guard\]|\[cleared by synthesis_guard\]"),
}


def shapers_fired(calls: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Which shaper markers each request carries (and L5: a request that dropped messages)."""
    fired: dict[str, list[int]] = {}
    previous = 0
    for index, call in enumerate(calls):
        text = "\n".join(_text(m) for m in call["messages"])
        for name, marker in SHAPER_MARKERS.items():
            if marker.search(text):
                fired.setdefault(name, []).append(index)
        if len(call["messages"]) < previous:
            fired.setdefault("L5 sliding window (or a compaction) dropped messages", []).append(index)
        previous = len(call["messages"])
    return fired


async def test_ls4_the_same_session_with_the_compiler_off_reduces_history_only_through_the_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events, transport = await _run(monkeypatch, "false")
    types = [e.type for e in events]
    assert types.count("run.completed") == 1
    # No legacy shaper cuts history on the durable loop: L4-L6 (QAFIX4 B2), L2, L3 and L8 (LONGTASK-F4) all cut it
    # without a journal entry. The shaped loop's ladder compacts instead, journaled, with the chat's row.
    fired = shapers_fired(transport.calls)
    sizes = [c["usage_tokens"] for c in _sizes(transport.calls)]
    dropped = "L5 sliding window (or a compaction) dropped messages"
    assert set(fired) <= {dropped}, (fired, sizes)
    assert "context.compacted" in types and "context.cleared" not in types
    assert transport.summaries == types.count("context.compacted")
    assert types.count("x.pipeshub.context.compacted") == types.count("context.compacted")


def _sizes(calls: list[dict[str, Any]]) -> list[dict[str, int]]:
    return [
        {"usage_tokens": (len(c["system"] or "") + sum(len(json.dumps(m.model_dump(mode="json"))) for m in c["messages"])) // 4}
        for c in calls
    ]


