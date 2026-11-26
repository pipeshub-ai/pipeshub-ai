"""PipesHub's half of the activity panel (UX5): the journal event a command's record becomes, and how a sandbox
tool's result is read back into a record when the backend could not stream it."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
from harness_core.contracts import validate_contract
from harness_core.journal.types import COMMAND_ACTIVITY, NewEvent

if TYPE_CHECKING:
    from pydantic import BaseModel

    from harness_core.activity import CommandRecord, CommandRecorder, CommandStatus
    from harness_core.journal.types import Claim

COMMAND_FINISHED_EVENT = "x.pipeshub.command.finished"


def command_finished_event(record: CommandRecord) -> NewEvent:
    data = record.as_data()
    model = EVENT_MODELS[COMMAND_FINISHED_EVENT]
    _ = validate_contract(cast("type[BaseModel]", model.model_fields["data"].annotation), data)
    return NewEvent(COMMAND_FINISHED_EVENT, data, critical=False)


def journals_commands(claim: Claim) -> bool:
    """An older Node refuses an event type it does not know (UX2): the record is journaled only when it lists it."""
    return claim.journal_accepts(COMMAND_ACTIVITY)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def finish_from_result(
    recorder: CommandRecorder, data: object, *, is_error: bool, streamed: bool
) -> CommandRecord:
    """A sandbox tool's result as a command record. A backend that could not stream (docker, e2b, local) hands its
    whole output over here, once."""
    fields: Mapping[str, object] = cast("Mapping[str, object]", data) if isinstance(data, Mapping) else {}
    if not streamed:
        for stream in ("stdout", "stderr"):
            text = fields.get(stream)
            if isinstance(text, str):
                recorder.write(stream, text)
        if is_error and isinstance(data, str):
            recorder.write("stderr", data)
    exit_code = _int(fields.get("exit_code"))
    duration = fields.get("duration_ms")
    status: CommandStatus = "failed" if is_error or (exit_code is not None and exit_code != 0) else "completed"
    return await recorder.finish(
        exit_code=exit_code,
        status=status,
        duration_ms=int(duration) if isinstance(duration, int | float) and not isinstance(duration, bool) else None,
    )


__all__ = ["COMMAND_FINISHED_EVENT", "command_finished_event", "finish_from_result", "journals_commands"]
