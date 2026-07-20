from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ImagePart,
    ImageSource,
    Message,
    MessageRole,
    Part,
    SystemMessage,
    TextPart,
    ThinkingPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.responses import (
    ModelResponse,
    RunUsage,
    StopReason,
    StructuredResponse,
    TokenUsage,
)

__all__ = [
    "MessageRole",
    "ToolCall",
    "Source",
    "ToolResult",
    "TokenUsage",
    "RunUsage",
    "ModelResponse",
    "StopReason",
    "StructuredResponse",
    "ImageSource",
    "TextPart",
    "ThinkingPart",
    "ImagePart",
    "Part",
    "SystemMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolMessage",
    "Message",
    "Confidence",
    "Intent",
    "Goal",
    "TodoStatus",
    "Todo",
    "AgentTurn",
    "ArtifactType",
    "Artifact",
    "AgentResult",
]


class Source(BaseModel):
    """Provenance for a claim (Phase 5 citations) — attached to a
    `ToolResult` by tools that fetch external information (web_search,
    web_scrape, knowledge_query, ...) so a writer/decision-trace/context-
    graph can trace an output back to where it came from, instead of only
    a role prompt convention asking the model to remember to cite. At
    least one of `url`/`file`/`query` should be set; `title` is a
    human-readable label for display."""

    url: str | None = None
    file: str | None = None
    query: str | None = None
    title: str | None = None


class ToolResult(BaseModel):
    tool_call_id: str
    name: str
    content: Any
    is_error: bool = False
    sources: list[Source] = Field(default_factory=list)


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Intent(BaseModel):
    raw_message: str
    parsed_intent: str
    context: dict[str, Any] = Field(default_factory=dict)


class Goal(BaseModel):
    description: str
    requirements: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    clarifications: dict[str, str] = Field(default_factory=dict)


class TodoStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class Todo(BaseModel):
    """One item of the in-loop task list the `write_todos` tool maintains
    (see tools/builtin/planning/todos.py) — the Planner's pre-loop, free-form
    `Plan.text` replaced by an agent-driven, updatable-mid-run list. Lives on
    `Agent._todos`, rendered into the "todos" PromptTemplate section every
    turn, and carried across pause/resume via `AgentCheckpoint.todos`."""

    content: str
    status: TodoStatus = TodoStatus.PENDING


class AgentTurn(BaseModel):
    messages: list[Message] = Field(default_factory=list)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)


class ArtifactType(str, Enum):
    TEXT = "text"
    JSON = "json"
    FILE = "file"
    IMAGE = "image"


class Artifact(BaseModel):
    """A named, typed output produced during a run — first-class alongside
    the free-text `output` field so downstream consumers (a handoff target,
    an orchestrator, a UI) can consume structured results without parsing
    prose. Modeled after the A2A protocol's Artifact concept: a run can emit
    zero or more of these via `task_complete(artifacts=[...])`."""

    name: str
    type: ArtifactType = ArtifactType.TEXT
    content: Any = None
    uri: str | None = None
    description: str | None = None


class AgentResult(BaseModel):
    goal: Goal
    output: Any = None
    artifacts: list[Artifact] = Field(default_factory=list)
    turns: list[AgentTurn] = Field(default_factory=list)
    success: bool = True
    error: str | None = None
    usage: RunUsage = Field(default_factory=RunUsage)
    # Optional structured contract a sub-agent's `task_complete` call can
    # populate on top of the always-present free-text `output` — never
    # required (many models degrade under forced structured output for
    # open-ended content, same rationale as `Plan.confidence` — see
    # `modules/pipeline/planner/base.py`), but a caller that DOES get them
    # can act on them without parsing prose:
    #   - `confidence`: the sub-agent's own confidence in `output`, distinct
    #     from a PLAN's confidence (`Plan.confidence`) — this is about the
    #     RESULT, checked by `verify_result`/a dependent deciding whether to
    #     trust this result at face value.
    #   - `record_ids`: machine-readable identifiers referenced in `output`
    #     (ticket keys, doc IDs, row IDs) — lets a dependent or the caller
    #     cross-reference without re-parsing prose for IDs.
    #   - `needs_input`: non-None means the sub-agent could NOT fully
    #     complete the goal because it is missing information only a HUMAN
    #     can supply — the structured escalation channel that replaces
    #     giving every sub-agent its own `ask_user_question` tool (see
    #     `TAG_UI_ONLY`): a child can never actually reach the user, so it
    #     reports what it needs here instead, and only the ROOT (which the
    #     user IS watching) decides whether/how to turn this into a real
    #     question.
    confidence: Confidence | None = None
    record_ids: list[str] = Field(default_factory=list)
    needs_input: str | None = None
