"""Where the grant auditor reads a run's pinned grant and effect log (P9)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from harness_core.audit.types import RunEvidence


class RunEvidenceSource(Protocol):
    async def evidence(self, org_id: str, run_id: str) -> RunEvidence | None:
        """The run's pinned grant and tool intents; ``None`` when ``org_id`` holds no such run. A run not yet
        pinned has an empty grant (no call may happen before the pin)."""
        ...


__all__ = ["RunEvidenceSource"]
