"""What a sandbox command looks like to the activity panel (UX5): its spec, its bounds and its record."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.journal.types import ContentRef

CommandSource = Literal["run_code", "engine"]
CommandStatus = Literal["completed", "failed", "timed_out", "cancelled"]
OutputStream = Literal["stdout", "stderr"]

COMMAND_STARTED = "command_started"
COMMAND_OUTPUT = "command_output"
COMMAND_FINISHED = "command_finished"
ACTIVITY_KINDS: tuple[str, ...] = (COMMAND_STARTED, COMMAND_OUTPUT, COMMAND_FINISHED)


@dataclass(frozen=True, slots=True)
class ActivityLimits:
    # One live frame's text; a slow bus merges queued output up to this size.
    frame_chars: int = 4096
    # Frames waiting for the bus; past it output is counted as not shown live, never awaited.
    queue_frames: int = 64
    live_chars_per_command: int = 256 * 1024
    tail_chars: int = 4000
    log_bytes: int = 1024 * 1024
    command_chars: int = 2000
    # Output held back until a line ends, so a secret split across two writes is redacted whole.
    hold_chars: int = 4096
    # A private key block is held to its END line; past this it is masked whole.
    key_hold_chars: int = 16 * 1024
    drain_timeout_s: float = 2.0


@dataclass(frozen=True, slots=True)
class CommandSpec:
    command_id: str
    source: CommandSource
    command: str
    invocation_id: str | None = None
    task_id: str | None = None
    language: str | None = None
    # Its engine can lose a command's output (R32ENGFU-FU-1): an empty record reads "No output was captured."
    output_may_be_lost: bool = False


@dataclass(frozen=True, slots=True)
class OutputTail:
    tail: str
    bytes: int
    truncated: bool

    def as_data(self) -> dict[str, JsonValue]:
        return {"tail": self.tail, "bytes": self.bytes, "truncated": self.truncated}


@dataclass(frozen=True, slots=True)
class CommandRecord:
    """A finished command, redacted and bounded: what the journal keeps and a reload shows."""

    spec: CommandSpec
    status: CommandStatus
    exit_code: int | None
    started_at_ms: int
    duration_ms: int | None
    stdout: OutputTail
    stderr: OutputTail
    live_dropped: int
    log: ContentRef | None = None
    log_truncated: bool = False

    def as_data(self) -> dict[str, JsonValue]:
        """The ``x.pipeshub.command.finished`` data (its schema's field names)."""
        data: dict[str, JsonValue] = {
            "commandId": self.spec.command_id,
            "source": self.spec.source,
            "command": self.spec.command,
            "status": self.status,
            "exitCode": self.exit_code,
            "startedAt": self.started_at_ms,
            "durationMs": self.duration_ms,
            "stdout": self.stdout.as_data(),
            "stderr": self.stderr.as_data(),
            "liveDropped": self.live_dropped,
        }
        optional: dict[str, JsonValue] = {
            "invocationId": self.spec.invocation_id,
            "taskId": self.spec.task_id,
            "language": self.spec.language,
        }
        data.update({k: v for k, v in optional.items() if v is not None})
        if self.spec.output_may_be_lost:
            data["outputMayBeLost"] = True
        if self.log is not None:
            data["log"] = self.log.model_dump(mode="json")
            data["logTruncated"] = self.log_truncated
        return data


__all__ = [
    "ACTIVITY_KINDS",
    "COMMAND_FINISHED",
    "COMMAND_OUTPUT",
    "COMMAND_STARTED",
    "ActivityLimits",
    "CommandRecord",
    "CommandSource",
    "CommandSpec",
    "CommandStatus",
    "OutputStream",
    "OutputTail",
]
