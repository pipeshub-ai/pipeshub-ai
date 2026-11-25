"""What a PipesHub run's verify gate (G-05, ``harness_core.gates.verify``) may look at.

- **Files**: the chat's workspace, as ``run_code`` reads and saves it (the same store an engine's turn saves to).
- **Citations**: the sources the run read, through the product's own citation normaliser: a ``[label](refN)`` resolves
  to exactly the passage the answer's citation card would show, or to nothing.
- **Commands**: the run's own ``run_code`` (offline, in the run's own sandbox, on the session workspace), the way a
  person's ``/run`` runs a line. Only where the run was granted ``run_code``; elsewhere a command check fails closed.

The checks are the harness's own, never the model's: they are not tool calls of the run, and their outcome is
journaled once, as ``verification.recorded``.
"""

from __future__ import annotations

import json
import shlex
from typing import TYPE_CHECKING, Any, cast

from app.harness.assembly.command_run import RUN_CODE, launcher
from harness_core.effects.types import ToolContext
from harness_core.gates.verify import CommandOutcome, VerifyPorts
from harness_core.kernel.core.messages import ToolCall

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.harness.assembly.run_code_workspace import RunCodeWorkspace
    from harness_core.invoker.ports import ToolCatalog

_TAIL_CHARS = 600


class RunCitationSources:
    """The passage a citation target names, among what the run read (its ``AgentContext`` citation state)."""

    def __init__(self, context: Any) -> None:  # noqa: ANN401 - the legacy AgentContext
        self._context = context

    async def source_text(self, target: str) -> str | None:
        from app.agents.agent_loop.hooks.citations import CitationCollector
        from app.utils.citations import normalize_citations_and_chunks

        collector = CitationCollector(self._context)
        mapper = collector.citation_ref_mapper
        _, citations = normalize_citations_and_chunks(
            f"[source]({target})",
            collector.final_results,
            collector.tool_records,
            ref_to_url=dict(mapper.ref_to_url) if mapper is not None else {},
            virtual_record_id_to_result=collector.virtual_records,
            web_records=collector.web_records,
        )
        if not citations:
            return None
        content = citations[0].get("content")
        return content if isinstance(content, str) and content else None


def _result(content: object) -> dict[str, Any]:
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return {}
    return cast("dict[str, Any]", content) if isinstance(content, dict) else {}


class RunCodeCommands:
    """A verify command through the run's ``run_code``: the command's exit status and its output's tail."""

    def __init__(self, tools: ToolCatalog, ctx: ToolContext) -> None:
        self._tools = tools
        self._ctx = ctx
        self._runs = 0

    async def run(self, argv: Sequence[str], *, timeout_s: int) -> CommandOutcome:
        self._runs += 1
        call_id = f"{self._ctx.invocation_id}-{self._runs}"
        call = ToolCall(
            id=call_id,
            name=RUN_CODE,
            arguments={"code": launcher(shlex.join(argv)), "language": "python", "timeout": float(timeout_s)},
        )
        ctx = ToolContext(
            self._ctx.run_id, self._ctx.org_id, call_id, self._ctx.attempt_id, f"{self._ctx.idempotency_key}-{self._runs}"
        )
        execution = await self._tools.execute(call, ctx)
        if execution.status == "timeout":
            return CommandOutcome(None, timed_out=True)
        result = _result(execution.content)
        code = result.get("exit_code")
        tail = "\n".join(str(result.get(k) or "") for k in ("stdout", "stderr", "error")).strip()[-_TAIL_CHARS:]
        return CommandOutcome(code if isinstance(code, int) else None, tail)


def pipeshub_verify_ports(
    *,
    context: Any,  # noqa: ANN401 - the legacy AgentContext
    workspace: RunCodeWorkspace | None,
    tools: ToolCatalog | None,
    has_run_code: bool,
    ctx: ToolContext,
) -> VerifyPorts:
    return VerifyPorts(
        # ``RunCodeWorkspace.read`` is the head's file exactly as a call is given it.
        files=workspace,
        citations=RunCitationSources(context),
        commands=RunCodeCommands(tools, ctx) if tools is not None and has_run_code else None,
    )


__all__ = ["RunCitationSources", "RunCodeCommands", "pipeshub_verify_ports"]
