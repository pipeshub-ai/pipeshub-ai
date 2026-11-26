"""An in-memory ``ClaimedKernel``: records the claims it is handed and the drains it is asked for."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.kernel.durable.driver import Disposition

if TYPE_CHECKING:
    from harness_core.journal.types import Claim


@dataclass
class RecordingKernel:
    disposition: Disposition = field(default_factory=lambda: Disposition("completed"))
    claims: list[Claim] = field(default_factory=list["Claim"])
    drains: int = 0

    async def run_claimed(self, claim: Claim, *, claim_sent_at: float) -> Disposition:
        self.claims.append(claim)
        return self.disposition

    def request_drain(self) -> None:
        self.drains += 1


__all__ = ["RecordingKernel"]
