# pyright: basic
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from harness_core.tools.base import Tag

EffectClass = Literal["read", "idempotent_write", "non_idempotent_write"]


class InvocationNeedsReconciliation(RuntimeError):
    """A prior non-idempotent effect started without a durable outcome."""


@dataclass(frozen=True, slots=True)
class InvocationStart:
    invocation_id: str
    cached_result: dict[str, Any] | None = None


class InvocationJournal(Protocol):
    async def begin(
        self,
        *,
        run_id: str,
        call_id: str,
        tool_name: str,
        tool_path: str,
        arguments: dict[str, Any],
        tags: tuple[Tag, ...],
    ) -> InvocationStart: ...

    async def finish(
        self,
        invocation_id: str,
        *,
        result: dict[str, Any],
        success: bool,
    ) -> None: ...

    async def interrupted(self, invocation_id: str) -> None: ...
