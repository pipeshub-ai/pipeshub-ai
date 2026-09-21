"""Stage protocol and the sequential pipeline runner."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from benchmarks.frames.services import RunContext

logger = logging.getLogger(__name__)


@dataclass
class StageReport:
    stage: str
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    elapsed_s: float = 0.0
    notes: list[str] = field(default_factory=list)


class Stage(Protocol):
    name: str

    def run(self, ctx: RunContext) -> StageReport: ...


def run_pipeline(stages: Sequence[Stage], ctx: RunContext) -> list[StageReport]:
    reports: list[StageReport] = []
    for stage in stages:
        started = time.monotonic()
        logger.info("stage %s: starting", stage.name)
        report = stage.run(ctx)
        report.elapsed_s = round(time.monotonic() - started, 2)
        logger.info(
            "stage %s: processed=%d skipped=%d failed=%d in %.1fs",
            stage.name, report.processed, report.skipped, report.failed, report.elapsed_s,
        )
        reports.append(report)
    return reports
