"""Golden journals (REPORT 6.1.6, L-6): what a durable run of each agent scenario journals.

Each P0.7 agent scenario runs through ``RunDriver``, the real assembler and
presenter, the streaming model port and a live bus; the journal (every event
envelope) and the live frames (each with the journal head it followed) are
frozen under ``contracts/harness/vectors/journals/``. Node's projector reads
the same files (``tests/contracts/durable-frames.test.ts``) to prove frame and
transcript parity with the legacy goldens, and writes the ``parts[]`` it
projects next to them; the Python fold must agree with those parts.

Record with ``UPDATE_GOLDENS=1`` (Python first, then Node), like the P0.7 goldens.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.assembly.prompt_shown import PROMPT_SHOWN_SLOT
from app.harness.presentation import HIDDEN_TOOLS
from harness_core.kernel.durable.transcript import decode_assistant
from harness_core.kernel.fold import fold_session
from tests.contracts.test_durable_goldens import (
    DURABLE_SCENARIOS,
    LOOP_HOOK_SCENARIOS,
    PLANNED_SCENARIOS,
    RUN,
    STEERED_SCENARIOS,
    STEERING,
    run_durably,
)

if TYPE_CHECKING:
    from harness_core.contracts.generated.common import ContentRef
    from harness_core.journal import InMemoryJournal, JournalEvent, LiveFrame
    from tests.contracts.golden_support import Scenario

# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

JOURNALS = (
    Path(__file__).resolve().parents[4]
    / "contracts"
    / "harness"
    / "vectors"
    / "journals"
)
SESSION = "ses-1"


class _RecordingBus:
    """A live bus that remembers each frame with the journal head it followed."""

    def __init__(self, journal: InMemoryJournal) -> None:
        self._journal = journal
        self.frames: list[dict[str, Any]] = []

    async def publish(self, frame: LiveFrame) -> None:
        self.frames.append(
            {
                "afterSeq": await self._journal.last_seq(SESSION),
                "frameId": frame.frame_id,
                "frame": {
                    "runId": frame.run_id,
                    "fence": frame.fence,
                    "modelAttempt": frame.model_attempt,
                    "n": frame.n,
                    "kind": frame.kind,
                    "data": frame.data,
                },
            }
        )


async def record(scenario: Scenario) -> dict[str, Any]:
    buses: list[_RecordingBus] = []

    def live(journal: InMemoryJournal) -> _RecordingBus:
        buses.append(_RecordingBus(journal))
        return buses[-1]

    events, _ = await run_durably(scenario, live, steer=STEERING.get(scenario.name))
    return {
        "scenario": scenario.name,
        "query": scenario.query,
        "runId": RUN,
        "sessionId": SESSION,
        "events": [_prompt_blind(e.envelope()) for e in events],
        "live": buses[0].frames if buses else [],
    }


def _prompt_blind(envelope: dict[str, Any]) -> dict[str, Any]:
    """The prompt's digest and size held fixed (R43MCP-FU-5's ``prompt.shown``): the goldens pin what a run journals,
    not the prompt's wording, which its own goldens pin; the source lines stay, as they name what was offered."""
    slots = (envelope.get("data") or {}).get("slots") if envelope.get("type") == "loop.state_saved" else None
    shown = slots.get(PROMPT_SHOWN_SLOT) if isinstance(slots, dict) else None
    if isinstance(shown, dict):
        shown.update({"stableDigest": "sha256:" + "0" * 64, "stableChars": 0})
    return envelope


def _dump(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


# A cut-off reply and its continuation (C-1): no legacy SSE golden, but Node
# reads the journal to show its parts join as legacy's do.
# Steered runs (T2) have no legacy golden either: Node reads them for the
# placement of the steering message, resume and audiences.
STEERED = [s for s, _ in STEERED_SCENARIOS]
RECORDED = (
    DURABLE_SCENARIOS
    + [s for s in LOOP_HOOK_SCENARIOS if s.name == "agent_truncated_response_continued"]
    + STEERED
    + PLANNED_SCENARIOS
)


@pytest.mark.parametrize("scenario", RECORDED, ids=[s.name for s in RECORDED])
async def test_the_golden_journal_is_what_a_durable_run_journals(
    scenario: Scenario,
) -> None:
    actual = _dump(await record(scenario))
    target = JOURNALS / f"{scenario.name}.journal.json"
    if os.environ.get("UPDATE_GOLDENS") == "1":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(actual, encoding="utf-8")
        return
    assert target.exists(), (
        f"missing golden journal {target.name}; record it with UPDATE_GOLDENS=1"
    )
    assert actual == target.read_text(encoding="utf-8"), (
        f"{target.name} drifted; re-record with UPDATE_GOLDENS=1 (then the Node projector) only if intended"
    )


def _events(doc: dict[str, Any]) -> list[JournalEvent]:
    from harness_core.journal import JournalEvent

    return [JournalEvent.from_envelope(raw) for raw in doc["events"]]


def _text(ref: ContentRef) -> str:
    import base64

    assert ref.ref.startswith("inline:")
    return base64.urlsafe_b64decode(ref.ref.removeprefix("inline:")).decode("utf-8")


def fold_skeleton(doc: dict[str, Any]) -> list[tuple[str, ...]]:
    """The run's timeline as the Python fold sees it: one text entry per model
    turn, then its tool calls in ``toolCalls`` order with their outcome status."""
    state = fold_session(_events(doc), stream_id=doc["sessionId"])
    skeleton: list[tuple[str, ...]] = []
    for entry in state.entries:
        if entry.input_seq is not None and entry.run_id == doc["runId"]:
            assert entry.text_ref is not None
            skeleton.append(("user_input", _text(entry.text_ref)))
            continue
        response = entry.response
        if response is None or entry.run_id != doc["runId"]:
            continue
        message = decode_assistant(_text(response.message_ref).encode("utf-8"))
        skeleton.append(("text", message.text))
        for call in response.tool_calls:
            if call.name in HIDDEN_TOOLS:
                continue
            result = entry.results.get(call.call_id)
            status = (
                "missing"
                if result is None
                else ("ok" if result.status == "ok" else "error")
            )
            skeleton.append(("tool_call", call.call_id, call.name, status))
    return skeleton


def projector_skeleton(parts: list[dict[str, Any]]) -> list[tuple[str, ...]]:
    """The same timeline read from the Node projector's ``parts[]`` (raw texts
    come from the journal: the projector's text parts carry the normalized answer)."""
    out: list[tuple[str, ...]] = []
    for part in parts:
        if part["type"] == "text":
            out.append(("text",))
        elif part["type"] == "tool_call":
            status = "ok" if part["status"] == "completed" else "error"
            out.append(("tool_call", part["toolCallId"], part["toolName"], status))
        elif part["type"] == "user_input":
            out.append(("user_input", part["content"]))
    return out


@pytest.mark.parametrize(
    "scenario", DURABLE_SCENARIOS + STEERED, ids=[s.name for s in DURABLE_SCENARIOS + STEERED]
)
def test_l6_the_python_fold_agrees_with_the_projector_parts(scenario: Scenario) -> None:
    journal = JOURNALS / f"{scenario.name}.journal.json"
    parts_file = JOURNALS / f"{scenario.name}.parts.json"
    assert parts_file.exists(), (
        f"missing {parts_file.name}: record the Node projector with UPDATE_GOLDENS=1"
    )
    doc = json.loads(journal.read_text(encoding="utf-8"))
    parts = json.loads(parts_file.read_text(encoding="utf-8"))["parts"]
    fold = fold_skeleton(doc)
    projected = projector_skeleton(parts)
    # A final answer the finalizer appended (no model text part) has no fold entry.
    if len(projected) == len(fold) + 1 and projected[-1] == ("text",):
        projected = projected[:-1]
    assert [item[:1] if item[0] == "text" else item for item in fold] == projected
