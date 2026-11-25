"""The durable kernel with the context compiler on: a host with bands, scripted usage."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from harness_core.context import HostBands, TruncationCompaction
from harness_core.kernel.durable.context_compiler import ContextCompiler
from harness_core.testing import Reply, ToolCallSpec
from harness_core.testing.model_types import Usage
from tests.unit.harness_core.durable.support import reply

from .support import tool

if TYPE_CHECKING:
    from harness_core.kernel.core.messages import Message, Part
    from harness_core.kernel.durable.ports import ModelCall
    from harness_core.kernel.fold import RunState


WINDOW = 10_000
SYSTEM = "You are the agent. Use the tools."


@dataclass
class Host:
    """Grants ``tools`` (more from ``granted_at[turn]`` on) and puts the turn in the tail."""

    tools: list[str] = field(default_factory=lambda: ["lookup", "create_issue"])
    granted_at: dict[int, list[str]] = field(default_factory=dict[int, list[str]])
    # How the host renders the run's own input (P4.6: PipesHub's goal), and parts appended to it (L0).
    input_text: str | None = None
    input_parts: tuple[Part, ...] = ()

    async def bands(self, call: ModelCall, turn: int, state: RunState) -> HostBands:
        names = list(self.tools)
        for since, more in self.granted_at.items():
            if turn >= since:
                names.extend(more)
        return HostBands(
            system=SYSTEM,
            tools=tuple(tool(n) for n in names),
            tail=(f"Now: turn {turn}.",),
            input_text=self.input_text,
            input_parts=self.input_parts,
        )


def compiler(host: Host | None = None, **overrides: Any) -> ContextCompiler:  # noqa: ANN401
    return ContextCompiler(
        window_tokens=overrides.pop("window_tokens", WINDOW),
        strategy=overrides.pop("strategy", TruncationCompaction()),
        host=host if host is not None else Host(),
        **overrides,
    )


def looked_up(tokens: int, q: str = "x") -> Reply:
    """A lookup turn the provider counted at ``tokens`` input tokens."""
    # A fixed call id: a re-claimed attempt's scripted model would otherwise number its calls afresh.
    return Reply(
        tool_calls=[ToolCallSpec(id=f"call-{q}", name="lookup", args={"q": q})],
        usage=Usage(input_tokens=tokens, output_tokens=10),
    )


def answered(tokens: int = 100, text: str = "done") -> Reply:
    return reply(text).model_copy(update={"usage": Usage(input_tokens=tokens, output_tokens=10)})


def dump(messages: list[Message]) -> list[str]:
    return [json.dumps(m.model_dump(mode="json"), sort_keys=True) for m in messages]
