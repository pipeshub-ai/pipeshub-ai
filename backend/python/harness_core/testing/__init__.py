"""Deterministic test kit for the harness (see README.md in this package)."""

from harness_core.testing.clock import ManualClock, TimerHandle
from harness_core.testing.failpoints import armed, write_arms
from harness_core.testing.fake_saas import (
    AckLost,
    Effect,
    FakeSaaS,
    IdempotencyConflict,
    RateLimit,
    RateLimited,
)
from harness_core.testing.ids import SeededIds
from harness_core.testing.model_script import (
    ErrorSpec,
    Expect,
    ModelScript,
    Reply,
    ToolCallSpec,
    Turn,
    Usage,
    load_script,
    parse_script,
)
from harness_core.testing.model_types import (
    Completed,
    ModelCallError,
    ModelEvent,
    ModelRequest,
    ModelResponse,
    ReasoningDelta,
    RequestMessage,
    ScriptExhausted,
    ScriptMismatch,
    StreamingModel,
    TextDelta,
    ToolCall,
    ToolCallEvent,
)
from harness_core.testing.scripted_model import ScriptedModel
from harness_core.testing.virtual_time import VirtualTimeLoop, run_on_virtual_time

__all__ = [
    "AckLost",
    "Completed",
    "Effect",
    "ErrorSpec",
    "Expect",
    "FakeSaaS",
    "IdempotencyConflict",
    "ManualClock",
    "ModelCallError",
    "ModelEvent",
    "ModelRequest",
    "ModelResponse",
    "ModelScript",
    "RateLimit",
    "RateLimited",
    "ReasoningDelta",
    "Reply",
    "RequestMessage",
    "ScriptExhausted",
    "ScriptMismatch",
    "ScriptedModel",
    "SeededIds",
    "StreamingModel",
    "TextDelta",
    "TimerHandle",
    "ToolCall",
    "ToolCallEvent",
    "ToolCallSpec",
    "Turn",
    "Usage",
    "VirtualTimeLoop",
    "armed",
    "load_script",
    "parse_script",
    "run_on_virtual_time",
    "write_arms",
]
