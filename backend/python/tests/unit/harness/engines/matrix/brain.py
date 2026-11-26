"""The scripted model of the matrix, which also answers the kernel's approval follow-up as a model would.

A held write ends the engine's turn; once a person decides, the engine kernel starts a follow-up turn whose user
message names each interaction and its decision (E2E3-F5). The conformance brain reads directives only, so it would
answer that turn ``done``; this one re-issues every approved call (found by the interaction id its deferred result
named), exactly once, then the prompt's ``after`` lines (calls made only once the approvals are decided, E2E4
RD-4: ``after tool <name> {...}``), then gives the original prompt's ``say`` text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from harness_core.testing.engines.script import (
    _CALLS as CALLS,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.testing.engines.script import (
    NO_TOOL_REPLY,
    ModelReply,
    ScriptedBrain,
    ToolCall,
    current_turn,
    parse_directives,
)
from harness_core.testing.engines.script import (
    _call_for as call_for,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.testing.engines.script import ChatMessage

DECIDED = "The approvals you were waiting for are decided"
AFTER = "after "
_APPROVED = re.compile(r" Approved: (.*?)\.(?= Not approved:| Call each)")


def _approved(note: str) -> list[str]:
    found = _APPROVED.search(note)
    return [ix.strip() for ix in found.group(1).split(",")] if found else []


@dataclass
class MatrixBrain(ScriptedBrain):
    @override
    def reply(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[str],
        call_id: str,
        properties: Mapping[str, frozenset[str]] | None = None,
    ) -> ModelReply:
        turn = current_turn(messages)
        note = next((m.content for m in turn if m.role == "user" and DECIDED in m.content), None)
        if note is None:
            return self._directed(messages, tools, call_id)
        made = sum(len(m.tool_calls) for m in turn if m.role == "assistant")
        wanted = [call for ix in _approved(note) if (call := _deferred_call(messages, ix)) is not None]
        if made < len(wanted):
            earlier = wanted[made]
            return ModelReply(tool_calls=(ToolCall(call_id, earlier.name, earlier.arguments),))
        first = next((m.content for m in messages if m.role == "user"), "")
        later = [
            d for line in first.splitlines() if line.strip().startswith(AFTER)
            for d in parse_directives(line.strip().removeprefix(AFTER)) if d.verb in CALLS
        ]
        if made - len(wanted) < len(later):
            call = call_for(later[made - len(wanted)], tools, call_id, messages)
            return ModelReply(tool_calls=(call,)) if call is not None else ModelReply(content=NO_TOOL_REPLY)
        said = [d.arg for d in parse_directives(first) if d.verb == "say"]
        return ModelReply(content=" ".join(said) if said else "done")

    def _directed(self, messages: Sequence[ChatMessage], tools: Sequence[str], call_id: str) -> ModelReply:
        """The conformance brain's directives, except that a held call does not end the turn: every engine then
        asks for every call of the prompt in the same turn (Codex and Claude Code show the held result as JSON,
        which the conformance brain reads as its cue to stop, opencode as text). A tool the list does not carry is
        called through ``call_tool`` once a ``search_tools`` result of the turn named it (an MCP server's tools are
        found that way, D-LAZY-4)."""
        turn = current_turn(messages)
        directives = [d for m in turn if m.role == "user" for d in parse_directives(m.content)]
        actions = [d for d in directives if d.verb in CALLS]
        made = sum(len(m.tool_calls) for m in turn if m.role == "assistant")
        if made < len(actions):
            call = call_for(actions[made], tools, call_id, messages)
            return ModelReply(tool_calls=(call,)) if call is not None else ModelReply(content=NO_TOOL_REPLY)
        said = [d.arg for d in directives if d.verb == "say"]
        return ModelReply(content=" ".join(said) if said else "done")


def _deferred_call(messages: Sequence[ChatMessage], interaction: str) -> ToolCall | None:
    """The call whose result told the engine it waits on ``interaction``."""
    result = next((m for m in messages if m.role == "tool" and interaction in m.content), None)
    if result is None:
        return None
    calls = [c for m in messages if m.role == "assistant" for c in m.tool_calls]
    return next((c for c in calls if c.id == result.tool_call_id), None)


__all__ = ["AFTER", "DECIDED", "MatrixBrain"]
