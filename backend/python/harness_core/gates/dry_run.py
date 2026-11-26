"""Dry run (G-03, GA-02): reads run, every outside effect is recorded as what the run would have done.

Enforced where calls are executed, never in the prompt: ``EffectRunner`` asks ``shadowed`` before it runs a call, and
both the durable loop and the Tool Gateway (``RunToolEndpoint``, an engine's only way to a tool) run calls through it.
A shadowed call is never handed to its tool: its outcome is ``simulated``, journaled as ``tool.outcome`` with
``shadow``, and its arguments ride the model response that asked for it. Nobody is asked to approve it, because
nothing happens; ``wouldAsk`` records that the call would have asked outside a dry run.

What stays the run's own runs as usual: a read, a change to the run's own workspace (``workspace``), and code in the
run's own sandbox with no network (``in_own_sandbox``). Everything else (a write, an execute that reaches out, a
destructive or financial tool whatever its class) is shadowed.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Final

from harness_core.effects.policy import in_own_sandbox
from harness_core.effects.types import ShadowMark, ToolExecution

if TYPE_CHECKING:
    from harness_core.effects.types import EffectCall, ToolEffect

# The ``tool.shadowed`` log line and the receipt-free outcome's own words.
DRY_RUN_RULE: Final = "dry_run"


def shadowed(effect: ToolEffect) -> bool:
    """A dry run never makes this call: it could change something outside the run's own workspace and sandbox."""
    if effect.mark is not None:
        return True
    if effect.effect in ("read", "workspace"):
        return False
    return not in_own_sandbox(effect)


def simulated(call: EffectCall, *, would_ask: bool) -> ToolExecution:
    """The outcome a dry run records for ``call``: what it would have done, never done. The model reads it as a
    result, so it carries on with the task and says what it would have done."""
    arguments = json.dumps(dict(call.call.arguments), ensure_ascii=False, sort_keys=True, default=str)
    if len(arguments) > 2000:
        arguments = arguments[:2000] + "…"
    asked = " It would have asked a person to approve it first." if would_ask else ""
    text = (
        f"Dry run: {call.call.name} was not called, and nothing was changed, sent or deleted. "
        f"This run is a dry run: it records what it would have done instead of doing it.{asked} "
        f"The call would have been made with: {arguments}. "
        "Carry on as if it had worked, and say in your answer what you would have done and that none of it was "
        "actually done."
    )
    return ToolExecution(status="ok", content=text, shadow=ShadowMark(would_ask=would_ask))


__all__ = ["DRY_RUN_RULE", "shadowed", "simulated"]
