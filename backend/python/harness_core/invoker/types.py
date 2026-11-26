"""Value types of an invocation: whose run it is, and why a call was refused."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from harness_core.effects.types import EffectResult

RefusalReason = Literal["not_granted"]
NOT_GRANTED: RefusalReason = "not_granted"


@dataclass(frozen=True)
class InvocationScope:
    """The run an Invoker serves. Opaque ids: the host allocates them."""

    run_id: str
    org_id: str
    attempt_id: str


@dataclass(frozen=True)
class ToolRefusal:
    """A call the Invoker would not run: nothing was executed or recorded."""

    reason: RefusalReason
    invocation_id: str
    tool_name: str
    message: str


type InvokeResult = EffectResult | ToolRefusal


__all__ = ["NOT_GRANTED", "InvocationScope", "InvokeResult", "RefusalReason", "ToolRefusal"]
