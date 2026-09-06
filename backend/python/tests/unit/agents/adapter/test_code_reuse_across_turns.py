"""The "add 5 more, keep the same style" path, end to end.

Reported failure: a styled PDF is generated, and the follow-up turn asking to
extend it produces a document with the styling lost. Two independent defects
caused it, and this suite pins both — each assertion fails on the pre-fix
code.

1. The transcript truncated the SERIALIZED JSON of a tool call's arguments at
   2000 chars. The Node gateway rebuilds a previous turn by running
   `JSON.parse` over that string and drops the arguments on failure, so a
   program over the cap did not arrive shortened — it vanished entirely, and
   the model had nothing to work from.

2. Nothing could read a code artifact's source back into the model's context.
   `input_artifacts` stages it as a file INSIDE the sandbox, readable by a
   program but invisible to the model writing that program.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from app.agent_loop_lib.events.base import AgentEvent, EventType, RunContext
from app.agents.actions.artifacts.artifacts import ArtifactManager
from app.agents.agent_loop.protocol.transcript_collector import TranscriptCollector
from app.models.entities import ArtifactType, LifecycleStatus
from app.services.artifact_registry import ArtifactMetadata

# A realistic generator: long enough to exceed the transcript cap, exactly
# like the 3322-char program in the production logs.
_JOKE_LINE = (
    'const JOKE_{i} = "Joke number {i}: a reasonably long setup line that runs '
    'to the sort of length real generated content actually has, followed by a '
    'punchline that also takes up a fair amount of room.";\n'
)

STYLED_PROGRAM = (
    'import { jsPDF } from "jspdf";\n'
    'const BRAND = { font: "Georgia", accent: "#5B2C6F", margin: 54 };\n'
    + "".join(_JOKE_LINE.format(i=i) for i in range(1, 11))
    + 'function render() {\n'
      '  const doc = new jsPDF({ unit: "pt", format: "letter" });\n'
      '  doc.setFont(BRAND.font, "bold");\n'
      '  doc.setTextColor(BRAND.accent);\n'
      '  doc.setFontSize(28);\n'
      '  doc.text("Five Jokes", BRAND.margin, 90);\n'
      '  let y = 150;\n'
      '  for (const j of [JOKE_1, JOKE_2, JOKE_3, JOKE_4, JOKE_5, JOKE_6, JOKE_7, JOKE_8, JOKE_9, JOKE_10]) {\n'
      '    doc.setFont(BRAND.font, "normal");\n'
      '    doc.setTextColor("#222222");\n'
      '    doc.setFontSize(13);\n'
      '    for (const line of doc.splitTextToSize(j, 460)) {\n'
      '      doc.text(line, BRAND.margin, y);\n'
      '      y += 20;\n'
      '    }\n'
      '    y += 18;\n'
      '  }\n'
      '  doc.save(`${process.env.OUTPUT_DIR}/jokes.pdf`);\n'
      '}\n'
      'render();\n'
)


async def _persisted_args(args: dict) -> str:
    """The `args` string that actually reaches Mongo for a run_code call.

    Goes through `TranscriptCollector.emit` rather than calling the
    serializer directly — the wiring at the call site is part of what
    broke, so a test that bypasses it would keep passing if that regressed.
    """
    ctx = RunContext(role_name="pipeshub-agent", model="gpt-4")
    collector = TranscriptCollector()
    await collector.emit(AgentEvent(event_type=EventType.RUN_STARTED, run_context=ctx, payload={}))
    await collector.emit(AgentEvent(
        event_type=EventType.TOOL_CALL_START, run_context=ctx,
        payload={"tool_call_id": "call_1", "tool": "run_code", "args": args},
    ))
    return next(p for p in collector.parts if p["type"] == "tool_call")["args"]


def _code_artifact_registry() -> MagicMock:
    """A registry holding the CODE artifact that turn 1 captured."""
    metadata = ArtifactMetadata(
        artifact_id="code-1", org_id="org-1", conversation_id="conv-1",
        name="code_dc3d848a3e04.ts", logical_name="code_dc3d848a3e04.ts",
        artifact_type=ArtifactType.CODE, mime_type="application/typescript",
        lifecycle_status=LifecycleStatus.PUBLISHED, version=1,
        size_bytes=len(STYLED_PROGRAM), document_id="doc-1",
    )
    registry = MagicMock()
    registry.resolve = AsyncMock(return_value=metadata)
    registry.get_content = AsyncMock(return_value=STYLED_PROGRAM.encode("utf-8"))
    return registry


def _manager(registry: MagicMock) -> ArtifactManager:
    manager = ArtifactManager({
        "org_id": "org-1", "user_id": "user-1", "conversation_id": "conv-1",
        "graph_provider": MagicMock(), "blob_store": MagicMock(),
    })
    manager._registry = lambda: registry
    return manager


class TestTurnOneTranscriptSurvivesToTurnTwo:
    def test_the_program_exceeds_the_transcript_cap(self) -> None:
        """Guards the premise: a test with a small program would pass even
        against the original bug."""
        assert len(STYLED_PROGRAM) > 2000

    async def test_node_can_replay_the_call(self) -> None:
        persisted = await _persisted_args({
            "code": STYLED_PROGRAM, "language": "typescript",
            "packages": ["jspdf"], "timeout": 60,
        })
        replayed = json.loads(persisted)  # what Node's JSON.parse does

        # Pre-fix this raised, and `factory.py::_convert_conversation_turn`
        # then built the historical ToolCall with arguments={}.
        assert replayed["language"] == "typescript"
        assert replayed["packages"] == ["jspdf"]
        assert replayed["code"], "the program must not be missing entirely"

    async def test_the_setup_needed_to_rerun_is_intact(self) -> None:
        """Language and package list are small and must never be collateral
        damage of shortening the code."""
        replayed = json.loads(await _persisted_args({
            "code": STYLED_PROGRAM, "language": "typescript", "packages": ["jspdf"],
        }))
        assert replayed["language"] == "typescript"
        assert replayed["packages"] == ["jspdf"]

    async def test_a_shortened_program_is_labelled_as_shortened(self) -> None:
        code = json.loads(await _persisted_args({"code": STYLED_PROGRAM}))["code"]
        if len(code) < len(STYLED_PROGRAM):
            assert "truncated" in code.lower()
            assert str(len(STYLED_PROGRAM)) in code


class TestTurnTwoCanReadTheSourceItMustEdit:
    async def test_full_source_is_retrievable(self) -> None:
        """The transcript is a bounded summary; the artifact is the source of
        truth. Whatever the transcript kept, the model can still get the whole
        program — which is what makes "keep the same style" achievable."""
        registry = _code_artifact_registry()
        success, payload = await _manager(registry).get_artifact_content(
            artifact_id="code-1",
        )

        assert success is True
        body = json.loads(payload)
        assert body["content"] == STYLED_PROGRAM
        assert body["truncated"] is False

    async def test_the_style_definitions_survive_the_round_trip(self) -> None:
        """The specific thing the user lost: fonts, colours, layout."""
        registry = _code_artifact_registry()
        _, payload = await _manager(registry).get_artifact_content(artifact_id="code-1")
        content = json.loads(payload)["content"]

        assert "Georgia" in content
        assert "#5B2C6F" in content
        assert "margin: 54" in content

    async def test_lineage_id_from_the_reminder_is_a_valid_ref(self) -> None:
        """The reminder gives the model a `derived_from_code_artifact_id`.
        That id has to work as-is — the model has no way to discover the
        code artifact's generated `code_<token>.ts` name."""
        registry = _code_artifact_registry()
        await _manager(registry).get_artifact_content(
            artifact_id="4bc1ae97-7b71-4766-ba1c-d8ff9ae4a23c",
        )
        assert registry.resolve.await_args.kwargs["ref"] == (
            "4bc1ae97-7b71-4766-ba1c-d8ff9ae4a23c"
        )
