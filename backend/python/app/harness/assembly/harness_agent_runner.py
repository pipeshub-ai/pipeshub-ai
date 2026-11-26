"""``HarnessAgentRunner`` (CM-02, design-plan §5.6): a coding engine as a ``spawn_agent`` worker in deep mode.

Deep mode's orchestrator spawns ``spawn_agent(role="coder")`` like any sub-agent: it takes part in the plan, waits on
the tasks it ``depends_on`` and their results reach it. It is decided as deep mode decides every spawn (the phase
gate, batch validation, waves, the budget share), then runs as an engine child under the run's pinned engine
authority, on a fork of the session workspace, as ``code_task``'s child does: the child inherits the principal and
narrows it, its token share is a slice of the parent's, and its taint joins the parent's with its result (CM-03,
CM-04). Its answer, files and citations come back as ``code_task``'s do. In this durable harness a worker is a child
run, not an in-process ``AgentRunner``: this is the ``DelegationTools`` that plays that role.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from app.agents.agent_loop.loops.orchestrator import PROGRAMMATIC_CALL_PREFIX
from app.harness.assembly.code_task import (
    CODER_ROLE,
    FILES_KEY,
    ONE_ENGINE_PER_MESSAGE,
    SPAWN_AGENT,
    is_engine_hand_off,
)
from harness_core.journal.children import (
    ChildExecutorRequest,
    ChildGrantRequest,
    ChildRequest,
)
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.kernel.durable.delegation import DelegatedOutcome, Refuse

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from app.harness.assembly.code_task import CodeTaskDelegation
    from app.harness.assembly.deep import DeepDelegation
    from harness_core.effects.types import ToolEffect
    from harness_core.journal.children import ChildRefused
    from harness_core.journal.types import NewEvent
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable.delegation import (
        Decision,
        Delegated,
        DelegationView,
    )
    from harness_core.workspace.forks import ChildMergeReport

CODER_HELP = (
    f' Or "{CODER_ROLE}": a coding engine that works in this conversation\'s workspace with its tools and a sandbox '
    "to run code; use it for a step that runs code or creates, reads or edits files. One coding step runs per "
    "message, counting code_task."
)


class HarnessAgentRunner:
    """Deep mode's ``spawn_agent``: a ``coder`` spawn runs on the engine, every other role as deep mode runs it."""

    def __init__(self, deep: DeepDelegation, engine: CodeTaskDelegation) -> None:
        self._deep = deep
        self._engine = engine

    def delegates(self, call: ToolCall) -> bool:
        return self._deep.delegates(call)

    def decide(self, call: Delegated, view: DelegationView) -> Decision:
        decided = self._deep.decide(call, view)
        if not isinstance(decided, ChildRequest) or not is_engine_hand_off(call.call):
            return decided
        at = next((i for i, d in enumerate(view.batch) if d.invocation_id == call.invocation_id), len(view.batch))
        if any(is_engine_hand_off(d.call) and d.state != "refused" for d in (*view.earlier, *view.batch[:at])):
            return Refuse(ONE_ENGINE_PER_MESSAGE)
        # The engine authority the run pinned decides what the child holds; deep mode's share of the tokens stays.
        return replace(
            decided,
            grants=ChildGrantRequest(mounts=("/workspace",), max_tokens=decided.grants.max_tokens),
            definition=None,
            executor=ChildExecutorRequest(),
        )

    async def outcome(
        self, call: ToolCall, document: JsonValue, *, workspace: ChildMergeReport | None = None
    ) -> DelegatedOutcome:
        if not is_engine_hand_off(call):
            return await self._deep.outcome(call, document, workspace=workspace)
        shown = await self._engine.outcome(call, document, workspace=workspace)
        if not call.id.startswith(PROGRAMMATIC_CALL_PREFIX):
            return shown
        # A plan step the director dispatched reads its result as text, as deep mode's other steps do.
        body = cast("dict[str, Any]", shown.content) if isinstance(shown.content, dict) else {}
        output = body.get("output")
        text = output if isinstance(output, str) else ""
        files: Sequence[str] = body.get(FILES_KEY) or []
        if files:
            text += "\n\n[FILES] " + ", ".join(files)
        if shown.error and not text:
            text = "the coding step failed"
            # WP-56: where its saved work was kept, so the director's answer can say so.
            workspace = body.get("workspace")
            unfinished = workspace.get("unfinished") if isinstance(workspace, dict) else None
            folder = unfinished.get("folder") if isinstance(unfinished, dict) else None
            if isinstance(folder, str) and folder:
                text += f"; what it had saved is in the folder “{folder}” in this conversation's files"
        return DelegatedOutcome(text, shown.error, shown.events)

    def refused(self, call: ToolCall, refusal: ChildRefused) -> tuple[NewEvent, ...]:
        return self._engine.refused(call, refusal) if is_engine_hand_off(call) else ()

    def effect(self, name: str) -> ToolEffect:
        return self._deep.effect(name)

    def schemas(self) -> list[ToolSchema]:
        return [_with_coder(s) if s.name == SPAWN_AGENT else s for s in self._deep.schemas()]


def _with_coder(schema: ToolSchema) -> ToolSchema:
    properties = dict(cast("dict[str, Any]", schema.input_schema.get("properties") or {}))
    role = dict(cast("dict[str, Any]", properties.get("role") or {"type": "string"}))
    role["description"] = str(role.get("description") or "") + CODER_HELP
    properties["role"] = role
    return ToolSchema(
        name=schema.name,
        description=schema.description + CODER_HELP,
        input_schema={**schema.input_schema, "properties": properties},
    )


__all__ = ["CODER_HELP", "HarnessAgentRunner"]
