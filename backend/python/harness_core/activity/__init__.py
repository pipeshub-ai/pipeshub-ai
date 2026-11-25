"""What a run does in its sandbox, for the activity panel (UX5): the command-output port and its bounded,
redacting implementation."""

from harness_core.activity.ports import CommandActivity, CommandRecorder
from harness_core.activity.redaction import SECRET_MASK, StreamRedactor, shared_redactor
from harness_core.activity.stream import (
    BoundedCommandActivity,
    current_command_recorder,
    recording,
)
from harness_core.activity.types import (
    ACTIVITY_KINDS,
    COMMAND_FINISHED,
    COMMAND_OUTPUT,
    COMMAND_STARTED,
    ActivityLimits,
    CommandRecord,
    CommandSource,
    CommandSpec,
    CommandStatus,
    OutputStream,
    OutputTail,
)

__all__ = [
    "ACTIVITY_KINDS",
    "COMMAND_FINISHED",
    "COMMAND_OUTPUT",
    "COMMAND_STARTED",
    "SECRET_MASK",
    "ActivityLimits",
    "BoundedCommandActivity",
    "CommandActivity",
    "CommandRecord",
    "CommandRecorder",
    "CommandSource",
    "CommandSpec",
    "CommandStatus",
    "OutputStream",
    "OutputTail",
    "StreamRedactor",
    "current_command_recorder",
    "recording",
    "shared_redactor",
]
