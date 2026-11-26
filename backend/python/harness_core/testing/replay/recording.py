"""The recording format: one run's model and tool I/O, in the order it happened.

A recording is plain JSON so a reviewer can read it in a PR. It carries
everything a replay needs to re-drive the harness without a model or a
connector: the run's inputs, every model response, every tool outcome, the
external effects the fakes saw and the final answer.
"""

# No ``from __future__ import annotations``: pydantic resolves these field
# types at class creation.
import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from harness_core.testing.model_types import Usage

FORMAT = "pipeshub.harness.recording/v1"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RecordedToolCall(_Frozen):
    id: str
    name: str
    args: dict[str, JsonValue] = {}


class RecordedResponse(_Frozen):
    text: str = ""
    reasoning: str = ""
    tool_calls: list[RecordedToolCall] = []
    usage: Usage = Usage()
    stop_reason: str = "end_turn"


class RecordedError(_Frozen):
    kind: str
    message: str = ""


class RequestSummary(_Frozen):
    """What the harness asked the model, reduced to what a reviewer needs.

    ``digest`` covers the non-system messages and the tool list; the system
    prompt is left out because it embeds the date, so a digest over it would
    differ on every day a recording is replayed.
    """

    message_count: int = 0
    last_user: str = ""
    last_tool: str | None = None
    last_tool_status: str | None = None
    tools: list[str] = []
    digest: str = ""


class ModelStep(_Frozen):
    kind: Literal["model"] = "model"
    request: RequestSummary = RequestSummary()
    response: RecordedResponse | None = None
    error: RecordedError | None = None


class ToolStep(_Frozen):
    kind: Literal["tool"] = "tool"
    call_id: str
    name: str
    args: dict[str, JsonValue] = {}
    status: Literal["succeeded", "failed", "denied"]
    result: JsonValue = None
    error: str | None = None
    sources: list[JsonValue] = []


Step = Annotated[ModelStep | ToolStep, Field(discriminator="kind")]


class RecordedEffect(_Frozen):
    """Something a fake external service committed (a post, a send, a fetch)."""

    op: str
    payload: dict[str, JsonValue] = {}


class RunInputs(_Frozen):
    route: str = "agent"
    chat_mode: str = "quick"
    query: str = ""
    fixtures: dict[str, JsonValue] = {}


class Manifest(_Frozen):
    scenario: str
    source: str = "legacy"
    harness: str = "legacy-agent-loop"
    model: str = "scripted-model"
    scrub_version: str | None = None


class FinalOutcome(_Frozen):
    answer: str = ""
    status: str = "completed"


class Recording(_Frozen):
    format: Literal["pipeshub.harness.recording/v1"] = FORMAT
    manifest: Manifest
    inputs: RunInputs = RunInputs()
    steps: list[Step] = []
    effects: list[RecordedEffect] = []
    final: FinalOutcome = FinalOutcome()

    def model_steps(self) -> list[ModelStep]:
        return [s for s in self.steps if isinstance(s, ModelStep)]

    def tool_steps(self) -> list[ToolStep]:
        return [s for s in self.steps if isinstance(s, ToolStep)]

    def to_json(self) -> str:
        return (
            json.dumps(self.model_dump(mode="json"), indent=2, ensure_ascii=False)
            + "\n"
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Recording":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


def digest(value: JsonValue) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "FORMAT",
    "FinalOutcome",
    "Manifest",
    "ModelStep",
    "RecordedEffect",
    "RecordedError",
    "RecordedResponse",
    "RecordedToolCall",
    "Recording",
    "RequestSummary",
    "RunInputs",
    "Step",
    "ToolStep",
    "digest",
]
