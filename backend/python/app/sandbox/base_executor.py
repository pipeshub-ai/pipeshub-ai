"""Abstract base class for sandbox executors."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

from app.sandbox.models import ArtifactOutput, ExecutionResult, detect_mime_type

logger = logging.getLogger(__name__)


class BaseExecutor(ABC):
    """Abstract base for code execution backends."""

    @abstractmethod
    async def execute(
        self,
        code: str,
        language: str,
        *,
        timeout_seconds: int = 60,
        packages: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Run *code* in the given *language* and return an ExecutionResult."""

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def collect_artifacts(output_dir: str) -> list[ArtifactOutput]:
        """Walk *output_dir* and return an ArtifactOutput for every file found."""
        artifacts: list[ArtifactOutput] = []
        if not os.path.isdir(output_dir):
            return artifacts

        for root, _dirs, files in os.walk(output_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    size = os.path.getsize(fpath)
                except OSError:
                    size = 0
                artifacts.append(
                    ArtifactOutput(
                        file_name=fname,
                        file_path=fpath,
                        mime_type=detect_mime_type(fname),
                        size_bytes=size,
                    )
                )
        return artifacts
