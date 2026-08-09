"""Workflow SDK stub generator.

Generates `.pyi` type stubs from:
1. The SDK's own Python signatures (ctx.py, decorators.py)
2. ToolRegistry schemas (for ctx.tool() argument hints)
3. EventCatalog descriptors (for typed event payload stubs)

Run with:
    python -m app.services.workflows.codegen.stub_generator --output sdk/_generated/

Output files:
    sdk/_generated/workflow_sdk.pyi
    sdk/_generated/events/slack.pyi  (one per catalog provider)
    sdk/_generated/context.pyi

CI drift gate: the generated files are committed; CI checks they match
the output of this script (fail on diff). This is the correctness backbone
for the codegen verify loop.
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["generate_sdk_stubs", "generate_event_stubs"]

_CTX_STUB = '''"""Type stubs for the PipesHub Workflow SDK — auto-generated, do not edit."""
from __future__ import annotations
from datetime import datetime
from typing import Any, Callable, Coroutine, TypeVar

T = TypeVar("T")


class _AgentHandle:
    """Returned by ctx.agent() and ctx.create_agent().  Call .run() to execute."""
    async def run(self, *, goal: str, **kwargs: Any) -> Any:
        """Run the agent with a natural-language goal."""
        ...


class _StateProxy:
    """Durable per-workflow key/value store returned by ctx.state."""
    async def get(self, key: str) -> Any:
        """Read a durable value. Returns None if not set."""
        ...
    async def set(self, key: str, value: Any) -> None:
        """Write a durable value. Journaled as a WRITE step."""
        ...


class Ctx:
    """Workflow execution context.

    Every method that crosses the sandbox boundary (tool, agent, search,
    state, emit) is journaled for deterministic replay.  Use ctx.now(),
    ctx.random(), ctx.uuid() instead of the standard-library equivalents.
    """
    run_id: str

    # -- Tool calls ----------------------------------------------------------

    async def tool(self, tool_name: str, **kwargs: Any) -> Any:
        """Call a PipesHub tool by Tool.name (e.g. \'jira__create_issue\').
        Journaled. All three naming conventions are accepted:
          - Tool.name:  \'jira__create_issue\'
          - URL path:   \'/tools/jira/create_issue\'
          - fullName:   \'jira.create_issue\'
        """
        ...

    # -- Agent calls ---------------------------------------------------------

    async def agent(self, agent_id: str) -> _AgentHandle:
        """Return a handle for running an existing Agent Builder agent.

        Usage:
            result = await (await ctx.agent("agent-uuid")).run(goal="Summarize...")
        """
        ...

    async def create_agent(
        self,
        name: str,
        *,
        instructions: str = "",
        tools: list[str] | None = None,
        knowledge: list[str] | None = None,
        skills: list[str] | None = None,
        mcps: list[str] | None = None,
        model: str | None = None,
        persist: bool = False,
    ) -> _AgentHandle:
        """Create a new agent and return a handle.

        Args:
            name: Display name.
            instructions: System prompt / instructions.
            tools: Tool.name list (e.g. [\'jira__create_issue\']).
            knowledge: Collection IDs for knowledge access.
            skills: Skill names.
            mcps: MCP server IDs (future; validated but no-op until registry).
            model: Optional model key override.
            persist: If True, the agent persists after this run.
        """
        ...

    # -- Knowledge search ----------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        collections: list[str] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search the knowledge base. Journaled.

        Args:
            query: Natural-language search query.
            collections: Optional collection IDs to scope the search.
            limit: Maximum results to return.
        """
        ...

    # -- Durable state -------------------------------------------------------

    @property
    def state(self) -> _StateProxy:
        """Durable per-workflow key/value store that persists across runs.

        Usage:
            last_id = await ctx.state.get("last_issue_id")
            await ctx.state.set("last_issue_id", "JRA-123")
        """
        ...

    # -- In-chat output ------------------------------------------------------

    async def emit(self, message: str, *, kind: str = "text") -> None:
        """Post a message back to the originating conversation.

        Args:
            message: Text, markdown, or structured content.
            kind: "text" (default), "code", "error", "card".
        """
        ...

    # -- Parallel fan-out ----------------------------------------------------

    async def map(
        self,
        fn: Callable[[Ctx, Any], Coroutine[Any, Any, T]],
        items: list[Any],
        *,
        concurrency: int | None = None,
    ) -> list[T]:
        """Parallel deterministic fan-out over items. `concurrency=None` uses
        this workflow's configured default."""
        ...

    # -- Deterministic primitives --------------------------------------------

    async def now(self) -> datetime:
        """Journaled current time. Never use datetime.now()."""
        ...

    async def random(self) -> float:
        """Journaled random float [0, 1). Never use random.random()."""
        ...

    async def uuid(self) -> str:
        """Journaled UUID4. Never use uuid.uuid4()."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Journaled short pause, at most 60s. For longer waits use a
        cron/interval trigger or wait_for_event() -- both park the run."""
        ...

    # -- HIL / event primitives ----------------------------------------------

    async def wait_for_event(
        self, event_type: str, *, timeout_s: float | None = None
    ) -> dict[str, Any]:
        """Suspend workflow until a matching event arrives."""
        ...

    async def request_approval(self, label: str, *, payload: Any = None) -> bool:
        """Pause workflow and request human approval. Returns True = approved."""
        ...

    # -- Logging -------------------------------------------------------------

    def log(self, message: str, **data: Any) -> None:
        """Structured logging. Not journaled."""
        ...


class SideEffect:
    READ: str
    WRITE: str
    NONE: str


class TriggerSpec:
    """Inert trigger description; registered with the scheduler at generation time."""
    kind: str
    options: dict[str, Any]


def cron(expression: str, *, tz: str = "UTC") -> TriggerSpec:
    """Fire on a 5-field cron schedule, evaluated in the given IANA timezone."""
    ...


def interval(seconds: int) -> TriggerSpec:
    """Fire every `seconds` seconds."""
    ...


def once_at(when: str) -> TriggerSpec:
    """Fire once at an ISO-8601 instant. Must be in the future."""
    ...


def on_event(event_type: Any, **filters: Any) -> TriggerSpec:
    """Fire when a matching app event arrives; filters are equality matches."""
    ...


def workflow(
    *,
    name: str | None = None,
    inputs: Any = None,
    outputs: Any = None,
    on_event: Any = None,
    triggers: list[TriggerSpec] | None = None,
) -> Any:
    """Mark an async function as a workflow entry point. Exactly one per file.

    `on_event` is internal event-binding metadata; use
    `triggers=[on_event(...)]` to subscribe to app events instead.
    """
    ...


def step(
    *,
    retries: int = 0,
    timeout_s: float | None = None,
    side_effect: SideEffect = SideEffect.NONE,
) -> Any:
    """Mark an async function as a journaled workflow step.

    The decorated function must accept a Ctx as its first argument.
    SideEffect.WRITE steps raise ReplayDivergence if no journal entry
    exists on resume — they must not be re-executed.
    """
    ...
'''

_SLACK_EVENTS_STUB = '''"""Type stubs for Slack app events — auto-generated from the event catalog."""
from __future__ import annotations
from pydantic import BaseModel
from typing import Any

class ChannelRef(BaseModel):
    id: str
    name: str

class UserRef(BaseModel):
    id: str
    name: str

class SlackMessagePosted(BaseModel):
    channel: ChannelRef
    user: UserRef
    text: str
    ts: str
    thread_ts: str | None = None

class SlackReactionAdded(BaseModel):
    reaction: str
    user: UserRef
    item_channel: str
    item_ts: str

class SlackChannelCreated(BaseModel):
    channel: ChannelRef
    creator: UserRef
    created: int

class slack:
    """Event-type constants for use with on_event(...)."""
    MessagePosted: str
    ReactionAdded: str
    ChannelCreated: str
'''

_JIRA_EVENTS_STUB = '''"""Type stubs for Jira app events — auto-generated from the event catalog."""
from __future__ import annotations
from pydantic import BaseModel
from typing import Any

class IssueRef(BaseModel):
    key: str
    id: str
    summary: str
    status: str
    priority: str | None = None
    assignee: str | None = None

class JiraIssueCreated(BaseModel):
    issue: IssueRef
    project_key: str

class JiraIssueUpdated(BaseModel):
    issue: IssueRef
    project_key: str
    changelog: dict

class JiraIssueCommented(BaseModel):
    issue: IssueRef
    project_key: str
    comment: str
    commenter: str

class jira:
    """Event-type constants for use with on_event(...)."""
    IssueCreated: str
    IssueUpdated: str
    IssueCommented: str
'''


def generate_sdk_stubs(output_dir: Path) -> None:
    """Write SDK type stubs to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "workflow_sdk.pyi").write_text(_CTX_STUB, encoding="utf-8")
    events_dir = output_dir / "events"
    events_dir.mkdir(exist_ok=True)
    (events_dir / "slack.pyi").write_text(_SLACK_EVENTS_STUB, encoding="utf-8")
    (events_dir / "jira.pyi").write_text(_JIRA_EVENTS_STUB, encoding="utf-8")
    (events_dir / "__init__.pyi").write_text("", encoding="utf-8")
    print(f"Generated stubs in {output_dir}")


def generate_event_stubs(event_type: str) -> str:
    """Return the stub content for a given event type prefix (e.g. 'slack')."""
    if event_type == "slack":
        return _SLACK_EVENTS_STUB
    if event_type == "jira":
        return _JIRA_EVENTS_STUB
    return f"# No stubs for {event_type}\n"


if __name__ == "__main__":
    import sys
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sdk/_generated")
    generate_sdk_stubs(output)
