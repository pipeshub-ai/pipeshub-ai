"""ITraceSink port — gen_ai vocabulary; composite Opik + OTel (§7)."""
from __future__ import annotations

from typing import Any, Protocol


class ITraceSink(Protocol):
    async def emit(self, event: str, run_id: str, data: dict[str, Any] | None = None) -> None:
        """Best-effort: must not raise. Implementations log and swallow."""
        ...
