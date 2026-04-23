"""Abstract base class for sandbox executors."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

from app.sandbox.models import ArtifactOutput, ExecutionResult, detect_mime_type

logger = logging.getLogger(__name__)


# Shared allowlist of host environment variables that are safe to inherit
# into a sandboxed execution. ANY variable outside this list is considered
# sensitive (API keys, credentials, JWT secrets, DB URLs, etc.) and MUST
# NOT be forwarded to user-controlled code — whether that code runs in a
# host subprocess (LocalExecutor) or in a Docker container (DockerExecutor).
SANDBOX_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH", "HOME", "USER", "TMPDIR", "TEMP", "TMP",
    "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "SHELL", "TERM",
    "PYTHONPATH", "NODE_PATH",
)


def build_sandbox_env(user_env: dict[str, str] | None) -> dict[str, str]:
    """Return a sandboxed environment dict: allowlist from host + user overrides.

    The allowlist comes from ``os.environ``; user-provided env vars (from
    the tool layer, already validated there) are layered on top so tools
    may still inject things like ``OUTPUT_DIR`` or ``DATABASE_URL``.
    """
    safe_env: dict[str, str] = {}
    for key in SANDBOX_ENV_ALLOWLIST:
        val = os.environ.get(key)
        if val is not None:
            safe_env[key] = val
    if user_env:
        safe_env.update(user_env)
    return safe_env


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
