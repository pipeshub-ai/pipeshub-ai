"""Ask blob storage whether a record's bytes are really gone.

The obvious check — call the document API and expect a 404 — cannot answer this
question. The API resolves a document through its MongoDB metadata, so once
that metadata is deleted the API returns 404 whether the bytes were removed or
merely orphaned. Those two outcomes are indistinguishable from outside and only
one of them is correct, so this module goes to the storage backend itself.

Which backend that is depends on how the instance was installed, so the probe
dispatches on the ``storageVendor`` recorded against each document rather than
assuming one. A vendor it cannot inspect raises instead of returning "clean" —
a cleanup probe that quietly passes when it cannot see anything is worse than
no probe at all, because it converts an unknown into a green tick.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from typing import Sequence

logger = logging.getLogger("blob-store-probe")

_DEFAULT_TIMEOUT = 120
_POLL_INTERVAL = 2.0

# Local storage writes under the user's home inside the container. The adapter
# picks the directory from the platform; the container is Linux, so it is
# always the XDG-ish path below.
_LOCAL_MOUNT_ROOT = os.getenv("PIPESHUB_LOCAL_STORAGE_ROOT", "/root/.local")
_LOCAL_MOUNT_NAME = os.getenv("PIPESHUB_LOCAL_STORAGE_MOUNT", "PipesHub")
_APP_CONTAINER = os.getenv("PIPESHUB_APP_CONTAINER", "pipeshub-ai")

_DOCKER_PREFIXES: Sequence[Sequence[str]] = (("docker",), ("sudo", "-n", "docker"))


class BlobProbeUnavailable(RuntimeError):
    """The probe could not inspect the backend, so it has no answer to give."""


class BlobStoreProbe:
    """Read-only questions about what blob storage still holds."""

    def __init__(self, container: str | None = None) -> None:
        self._container = container or _APP_CONTAINER

    # ------------------------------------------------------------------ #
    # Local storage
    # ------------------------------------------------------------------ #

    def _docker_exec(self, script: str) -> str:
        errors: list[str] = []
        for prefix in _DOCKER_PREFIXES:
            try:
                result = subprocess.run(
                    [*prefix, "exec", "-i", self._container, "sh", "-c", script],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
            except Exception as exc:  # noqa: BLE001 - try the next invocation
                errors.append(f"{' '.join(prefix)}: {exc}")
                continue
            if result.returncode == 0:
                return result.stdout
            errors.append(
                f"{' '.join(prefix)}: {(result.stderr or result.stdout)[:200]}"
            )
        raise BlobProbeUnavailable(
            "Could not read local blob storage through docker exec on container "
            f"{self._container!r}:\n  " + "\n  ".join(errors)
        )

    def _local_files_under(self, relative_path: str) -> list[str]:
        base = f"{_LOCAL_MOUNT_ROOT}/{_LOCAL_MOUNT_NAME}"
        target = f"{base}/{relative_path}".rstrip("/")
        # `|| true` so a missing directory is an empty answer, not a failure:
        # the directory being gone is exactly what a passing cleanup looks like.
        out = self._docker_exec(
            f"find '{target}' -type f 2>/dev/null || true"
        )
        return [line for line in out.splitlines() if line.strip()]

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def files_under(self, document_path: str, vendor: str = "local") -> list[str]:
        """Every stored file beneath a document path.

        ``document_path`` is the value MongoDB records against the document,
        which is a directory prefix rather than a single file — one record's
        bytes are spread across ``current/`` and ``versions/``.
        """
        normalised = (vendor or "local").strip().lower()
        if normalised == "local":
            return await asyncio.to_thread(self._local_files_under, document_path)
        raise BlobProbeUnavailable(
            f"No blob probe implemented for storage vendor {vendor!r}. The "
            "test cannot confirm the bytes were removed, and reporting that as "
            "a pass would be wrong — implement the vendor or skip the test "
            "explicitly."
        )

    async def count_under(self, document_path: str, vendor: str = "local") -> int:
        return len(await self.files_under(document_path, vendor))

    # ------------------------------------------------------------------ #
    # Assertions
    # ------------------------------------------------------------------ #

    async def assert_blobs_gone(
        self,
        document_path: str,
        vendor: str = "local",
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        files = await self.files_under(document_path, vendor)
        while files and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            files = await self.files_under(document_path, vendor)
        if files:
            shown = "\n  ".join(files[:5])
            more = f"\n  …and {len(files) - 5} more" if len(files) > 5 else ""
            raise AssertionError(
                f"{len(files)} file(s) remain in blob storage under "
                f"{document_path!r} after {timeout}s:\n  {shown}{more}\n"
                "The metadata may already be gone, which makes these "
                "unreachable through the API and invisible to any test that "
                "checks the API alone."
            )

    async def assert_blobs_present(
        self, document_path: str, vendor: str = "local"
    ) -> list[str]:
        files = await self.files_under(document_path, vendor)
        assert files, (
            f"No files found in blob storage under {document_path!r} before the "
            "delete. A cleanup test has to start from something."
        )
        return files

    async def assert_blobs_survive(
        self, document_path: str, vendor: str = "local", expected_min: int = 1
    ) -> None:
        """A duplicate still refers to these bytes, so they must stay."""
        files = await self.files_under(document_path, vendor)
        assert len(files) >= expected_min, (
            f"Expected at least {expected_min} file(s) to survive under "
            f"{document_path!r}, found {len(files)}. Another record still "
            "points at this content, and removing it leaves that record "
            "present in the graph with nothing behind it."
        )
