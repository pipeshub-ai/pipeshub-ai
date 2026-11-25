"""Which tool calls delegate to a durable child run, and what each one decides (P6.1, K-L).

The kernel asks a ``DelegationTools`` registry, never a tool name: a call it
answers for becomes a child admission (``ChildRunPort.spawn``) and its outcome
is the child's result. Before admitting, the registry decides each pending
call against the run's delegations so far (``DelegationView``): spawn it,
refuse it (its outcome is the refusal), or wait until calls it depends on have
joined. ``NamedDelegation`` is the registry for tools whose arguments name the
child's goal; deep mode registers ``spawn_agent`` with children built from
agent definitions (``app.harness.assembly.deep``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import JsonValue

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.types import ToolEffect, tool_id_for
from harness_core.journal.children import (
    BudgetShare,
    ChildExecutorRequest,
    ChildGrantRequest,
    ChildRequest,
    child_budget_share,
)
from harness_core.kernel.core.tool_schema import ToolSchema

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.journal.types import NewEvent
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.workspace.forks import ChildMergeReport

DelegatedState = Literal["pending", "joining", "joined", "refused"]
# Where a joined child's merge report sits in the outcome the model reads.
WORKSPACE_KEY = "workspace"


@dataclass(frozen=True)
class Delegated:
    """One delegating call of the run. ``document``: its child's result once
    joined; ``outcome``: the content its journaled outcome holds (a refusal's
    text, or what the model read of a joined child)."""

    invocation_id: str
    call: ToolCall
    turn: int
    state: DelegatedState
    document: JsonValue | None = None
    outcome: JsonValue | None = None


@dataclass(frozen=True)
class Gathered:
    """A completed call of an earlier turn that delegated nothing: what it asked and the
    content its outcome holds (what the model read of it), so a child can start from it."""

    call: ToolCall
    turn: int
    status: str | None
    outcome: JsonValue


@dataclass(frozen=True)
class DelegationView:
    """The run's delegations at a dispatch: ``batch`` this turn's, in ``toolCalls``
    order; ``earlier`` every earlier turn's; ``slots`` the run's journaled slots;
    ``gathered`` every other call of an earlier turn, in order."""

    batch: tuple[Delegated, ...]
    earlier: tuple[Delegated, ...] = ()
    slots: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    gathered: tuple[Gathered, ...] = ()


@dataclass(frozen=True)
class Refuse:
    """The call is not delegated; ``content`` is its error outcome."""

    content: JsonValue


@dataclass(frozen=True)
class Wait:
    """The call waits until the named invocations of its batch have joined."""

    on: tuple[str, ...]


Decision = ChildRequest | Refuse | Wait


@dataclass(frozen=True)
class DelegatedOutcome:
    """What a joined child's call journals: the content the model reads, whether
    it is an error, and product events journaled with it."""

    content: JsonValue
    error: bool
    events: tuple[NewEvent, ...] = ()


class DelegationTools(Protocol):
    def delegates(self, call: ToolCall) -> bool:
        """Whether the call delegates to a child run."""
        ...

    def decide(self, call: Delegated, view: DelegationView) -> Decision:
        """For a pending call: its child, a refusal, or a wait. A pure function of
        ``view``, so a re-claimed attempt decides the same."""
        ...

    async def outcome(
        self, call: ToolCall, document: JsonValue, *, workspace: ChildMergeReport | None = None
    ) -> DelegatedOutcome:
        """The joined child's result document as the call's outcome; ``workspace``:
        what merging the child's fork did (P6.4), which the model must read too."""
        ...

    def effect(self, name: str) -> ToolEffect:
        """How the call is journaled (`tool.intent`); spawning is idempotent per invocation."""
        ...

    def schemas(self) -> list[ToolSchema]:
        """What the model is shown for the delegating tools."""
        ...


# A task id the journal can carry (common `Id`); a model-written one that is not is dropped.
TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
# Spawning writes nothing outside PipesHub and Node replays it per invocation.
DELEGATE_SINK = SinkDescriptor(egress="none", args_leave_boundary=False)


def delegate_effect(name: str) -> ToolEffect:
    return ToolEffect(
        tool_id=tool_id_for(name, source="delegate"),
        effect="read",
        idempotent=True,
        sink=DELEGATE_SINK,
    )


class ChildBudget(Protocol):
    """The one place a delegating registry asks what budget a child gets.
    Until P6.5's ledger binds here, ``InterimChildBudget`` answers with
    ``child_budget_share``."""

    def share(
        self,
        *,
        parent_max_turns: int,
        parent_max_tokens: int,
        definition_max_turns: int,
        batch: int,
    ) -> BudgetShare: ...


class InterimChildBudget:
    def share(
        self,
        *,
        parent_max_turns: int,
        parent_max_tokens: int,
        definition_max_turns: int,
        batch: int,
    ) -> BudgetShare:
        return child_budget_share(
            parent_max_turns=parent_max_turns,
            parent_max_tokens=parent_max_tokens,
            definition_max_turns=definition_max_turns,
            batch=batch,
        )


def is_success(document: JsonValue) -> bool:
    return isinstance(document, dict) and document.get("success") is True


@dataclass(frozen=True)
class DelegatingTool:
    """One delegating tool: its schema, the grant its children get (``None``: the parent's),
    and what they run on (``None``: the loop; an engine for a ``code_task``, E1)."""

    schema: ToolSchema
    grants: ChildGrantRequest = field(default_factory=ChildGrantRequest)
    executor: ChildExecutorRequest | None = None


def goal_tool(name: str, description: str) -> ToolSchema:
    """A tool whose arguments are the child's goal and an optional task id."""
    return ToolSchema(
        name=name,
        description=description,
        input_schema={
            "type": "object",
            "properties": {
                "goal": {"type": "string", "description": "What the sub-agent must accomplish"},
                "task_id": {"type": "string", "description": "A short stable name for the task"},
            },
            "required": ["goal"],
        },
    )


class NamedDelegation:
    """Delegating tools by name; the model's arguments name the goal, never the grant."""

    def __init__(self, tools: Mapping[str, DelegatingTool]) -> None:
        self._tools = dict(tools)

    def delegates(self, call: ToolCall) -> bool:
        return call.name in self._tools

    def decide(self, call: Delegated, view: DelegationView) -> Decision:
        tool = self._tools[call.call.name]
        goal = call.call.arguments.get("goal")
        task = call.call.arguments.get("task_id")
        return ChildRequest(
            invocation_id=call.invocation_id,
            goal=goal if isinstance(goal, str) and goal.strip() else "(no goal given)",
            grants=tool.grants,
            task_id=task if isinstance(task, str) and TASK_ID.match(task) else None,
            executor=tool.executor,
        )

    async def outcome(
        self, call: ToolCall, document: JsonValue, *, workspace: ChildMergeReport | None = None
    ) -> DelegatedOutcome:
        shown: JsonValue = document
        if workspace is not None and isinstance(document, dict):
            report: JsonValue = workspace.as_json()
            shown = {**document, WORKSPACE_KEY: report}
        return DelegatedOutcome(shown, error=not is_success(document))

    def effect(self, name: str) -> ToolEffect:
        return delegate_effect(name)

    def schemas(self) -> list[ToolSchema]:
        return [tool.schema for tool in self._tools.values()]


__all__ = [
    "DELEGATE_SINK",
    "ChildBudget",
    "TASK_ID",
    "WORKSPACE_KEY",
    "Decision",
    "DelegatedOutcome",
    "Delegated",
    "DelegatedState",
    "DelegatingTool",
    "DelegationTools",
    "DelegationView",
    "Gathered",
    "InterimChildBudget",
    "NamedDelegation",
    "Refuse",
    "Wait",
    "delegate_effect",
    "goal_tool",
    "is_success",
]
