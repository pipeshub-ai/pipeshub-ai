"""``BridgeConnectWait``: an engine's call of an unconnected item waits for its owner to connect it (G-12).

The engine's half of the loop's connect wait (``kernel.durable.connect``). The
gateway answers the call ``deferred`` with ``reason_code`` ``auth_required``
and the interaction it waits on; the engine kernel suspends on it as on an
approval. The interaction is named by the call (run, tool, arguments), not its
invocation, so the engine's re-call after the decision finds it (as
``BridgeApprovalHold`` does). Nothing waits inside the gateway: its tools were
assembled before the credential existed, and the attempt after the resume
assembles them with it, where the call no longer needs connecting and runs once
under the run's rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from harness_core.contracts import canonical_sha256
from harness_core.interactions.ports import InteractionRequest

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.interactions.connect import ConnectGate, ConnectNeed
    from harness_core.interactions.ports import InteractionBridge, RunBinding
    from harness_core.kernel.core.messages import ToolCall

AUTH_REQUIRED = "auth_required"


@dataclass(frozen=True, slots=True)
class ConnectAnswer:
    """``waiting``: the call is deferred on ``interaction_id``; ``declined``: refused with ``text``."""

    state: Literal["waiting", "declined"]
    interaction_id: str
    need: ConnectNeed
    text: str = ""


def connect_interaction_id(run_id: str, name: str, arguments: Mapping[str, JsonValue]) -> str:
    digest = canonical_sha256({"runId": run_id, "tool": name, "arguments": dict(arguments)})
    return f"ix.connect.k{digest[:40]}"


@dataclass(frozen=True)
class BridgeConnectWait:
    bridge: InteractionBridge
    binding: RunBinding
    gate: ConnectGate
    # Items the person chose not to connect during this attempt (skipped, or not in time): a later call of one is
    # refused at once and the engine goes on without it, never asked again (WP-64).
    skipped: set[str] = field(default_factory=set, compare=False, hash=False)

    async def answer(self, call: ToolCall) -> ConnectAnswer | None:
        """``None``: the call needs nothing connected, or its owner said they connected it; it goes on as any call."""
        need = self.gate.need(call)
        if need is None:
            return None
        ix = connect_interaction_id(self.binding.run_id, call.name, call.arguments)
        if need.instance_id in self.skipped:
            return ConnectAnswer("declined", ix, need, self.gate.declined(call))
        ticket = await self.bridge.request(
            self.binding,
            InteractionRequest(ix, "connect", call.name, canonical_sha256(dict(call.arguments)), connect=need),
        )
        decision = await self.bridge.wait(ticket, within_s=0)
        if decision is None:
            return ConnectAnswer("waiting", ix, need)
        if decision == "approved":
            # Said connected, yet this attempt's tools still lack it: the call answers how to connect it, once.
            return None
        self.skipped.add(need.instance_id)
        return ConnectAnswer("declined", ix, need, self.gate.declined(call))


__all__ = ["AUTH_REQUIRED", "BridgeConnectWait", "ConnectAnswer", "connect_interaction_id"]
