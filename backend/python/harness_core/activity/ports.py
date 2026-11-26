"""The command-output port (UX5): what a sandbox command printed, live for the activity panel and bounded for the
journal. One ``CommandActivity`` per run attempt; one ``CommandRecorder`` per command."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from harness_core.activity.types import (
        CommandRecord,
        CommandSpec,
        CommandStatus,
        OutputStream,
    )


class CommandRecorder(Protocol):
    def write(self, stream: OutputStream, text: str) -> None:
        """Never blocks and never raises: output the bus cannot take now is counted, not awaited."""
        ...

    async def finish(
        self, *, exit_code: int | None, status: CommandStatus, duration_ms: int | None = None
    ) -> CommandRecord:
        """The command's redacted, bounded record (its full log stored once); ``duration_ms`` defaults to the
        recorder's own clock."""
        ...


class CommandActivity(Protocol):
    def begin(self, spec: CommandSpec) -> CommandRecorder: ...

    async def aclose(self) -> None:
        """Publishes what is queued (a bounded wait) and stops."""
        ...


__all__ = ["CommandActivity", "CommandRecorder"]
