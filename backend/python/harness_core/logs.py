"""Harness log lines: an event, a reason code where one applies, and correlation fields.

Ground rule 9: a run is reconstructable by grepping its ``runId``. Every line
carries whichever of ``orgId``, ``sessionId``, ``runId``, ``attemptId``,
``fence``, ``seq``, ``commandId``, ``invocationId`` and ``requestId`` apply, in
the message and in ``extra["harness"]`` for structured handlers.

Callers pass ids, sizes, counts, digests and reason codes only: never prompt,
message or tool content, tool arguments or results, tokens or credentials.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.journal.types import AttemptToken, Claim

LogValue = str | int | float | bool | None
Field = LogValue | tuple[str, ...] | list[str]

CORRELATION = (
    "orgId",
    "sessionId",
    "runId",
    "attemptId",
    "fence",
    "seq",
    "commandId",
    "invocationId",
    "requestId",
)


def _render(value: Field) -> str:
    if isinstance(value, list | tuple):
        return "[" + ",".join(str(item) for item in value) + "]"
    return str(value)


class HarnessLog:
    """A logger bound to a run's correlation fields."""

    def __init__(self, logger: logging.Logger, fields: Mapping[str, Field] | None = None) -> None:
        self._logger = logger
        self._fields: dict[str, Field] = {
            key: value for key, value in (fields or {}).items() if value is not None
        }

    @classmethod
    def named(cls, name: str, **fields: Field) -> HarnessLog:
        return cls(logging.getLogger(name), fields)

    @classmethod
    def for_claim(cls, name: str, claim: Claim) -> HarnessLog:
        return cls.named(
            name,
            orgId=claim.org_id,
            sessionId=claim.session_id,
            runId=claim.run_id,
            attemptId=claim.attempt_id,
            fence=claim.fence,
        )

    @classmethod
    def for_token(cls, name: str, token: AttemptToken, **fields: Field) -> HarnessLog:
        return cls.named(
            name, runId=token.run_id, attemptId=token.attempt_id, fence=token.fence, **fields
        )

    @property
    def fields(self) -> dict[str, Field]:
        return dict(self._fields)

    def bind(self, **fields: Field) -> HarnessLog:
        return HarnessLog(self._logger, {**self._fields, **fields})

    def debug(self, event: str, **fields: Field) -> None:
        self._emit(logging.DEBUG, event, fields)

    def info(self, event: str, **fields: Field) -> None:
        self._emit(logging.INFO, event, fields)

    def warning(self, event: str, **fields: Field) -> None:
        self._emit(logging.WARNING, event, fields)

    def error(self, event: str, *, exc: BaseException | None = None, **fields: Field) -> None:
        self._emit(logging.ERROR, event, fields, exc)

    def _emit(
        self,
        level: int,
        event: str,
        fields: Mapping[str, Field],
        exc: BaseException | None = None,
    ) -> None:
        if not self._logger.isEnabledFor(level):
            return
        merged = {**self._fields, **{k: v for k, v in fields.items() if v is not None}}
        ordered: Sequence[str] = [
            *(key for key in CORRELATION if key in merged),
            *sorted(key for key in merged if key not in CORRELATION),
        ]
        text = " ".join([event, *(f"{key}={_render(merged[key])}" for key in ordered)])
        self._logger.log(
            level,
            text,
            exc_info=(type(exc), exc, exc.__traceback__) if exc is not None else None,
            extra={"harness_event": event, "harness": merged},
        )


__all__ = ["CORRELATION", "HarnessLog"]
