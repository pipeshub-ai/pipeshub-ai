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

# Compose does not pin container_name for this service — the installer tests
# forbid it — so the running container is named "{project}-pipeshub-ai-1" and
# the project varies by checkout. Guessing a bare "pipeshub-ai" finds nothing,
# and a probe that cannot reach the container must say so rather than report an
# empty directory, which reads as a successful cleanup.
_APP_SERVICE_SUFFIX = "-pipeshub-ai-1"
_APP_CONTAINER_ENV = "PIPESHUB_APP_CONTAINER"

_DOCKER_PREFIXES: Sequence[Sequence[str]] = (("docker",), ("sudo", "-n", "docker"))

# `find` on a directory that is not there is the shape of a cleaned-up record,
# so that one case is an empty answer. Every other failure is a probe that did
# not run, and must not be reported as "no files".
_MISSING_DIRECTORY_EXIT = 3


class BlobProbeUnavailable(RuntimeError):
    """The probe could not inspect the backend, so it has no answer to give."""


class BlobStoreProbe:
    """Read-only questions about what blob storage still holds."""

    def __init__(self, container: str | None = None) -> None:
        self._container = container or os.getenv(_APP_CONTAINER_ENV) or None
        self._resolved: str | None = self._container

    # ------------------------------------------------------------------ #
    # Local storage
    # ------------------------------------------------------------------ #

    def _run_docker(self, args: Sequence[str]) -> subprocess.CompletedProcess:
        """Run a docker command, trying sudo second, and return the attempt.

        Returns the last attempt rather than raising so callers can decide what
        a non-zero exit means — for `find`, one exit code is a valid answer.
        """
        last: subprocess.CompletedProcess | None = None
        errors: list[str] = []
        for prefix in _DOCKER_PREFIXES:
            try:
                last = subprocess.run(
                    [*prefix, *args], capture_output=True, text=True, timeout=60
                )
            except Exception as exc:  # noqa: BLE001 - try the next invocation
                errors.append(f"{' '.join(prefix)}: {exc}")
                continue
            if last.returncode == 0 or last.returncode == _MISSING_DIRECTORY_EXIT:
                return last
            errors.append(
                f"{' '.join(prefix)}: exit {last.returncode} "
                f"{(last.stderr or last.stdout)[:200]}"
            )
        if last is None:
            raise BlobProbeUnavailable(
                "Could not run docker at all:\n  " + "\n  ".join(errors)
            )
        return last

    def container(self) -> str:
        """The running app container, resolved once.

        Raises rather than guessing. An unresolvable container means the probe
        cannot look at blob storage, and the one thing it must never do is
        answer "no files" without having looked.
        """
        if self._resolved:
            return self._resolved

        result = self._run_docker(
            ["ps", "--filter", "status=running", "--format", "{{.Names}}"]
        )
        if result.returncode != 0:
            raise BlobProbeUnavailable(
                "Could not list running containers to find the PipesHub app:\n"
                f"  {(result.stderr or result.stdout)[:300]}"
            )

        matches = [
            name
            for name in (line.strip() for line in result.stdout.splitlines())
            if name.endswith(_APP_SERVICE_SUFFIX)
        ]
        if len(matches) == 1:
            self._resolved = matches[0]
            logger.debug("Resolved app container as %s", self._resolved)
            return self._resolved

        if not matches:
            raise BlobProbeUnavailable(
                "No running container name ends with "
                f"{_APP_SERVICE_SUFFIX!r}, so blob storage cannot be inspected. "
                f"Set {_APP_CONTAINER_ENV} to the container name."
            )
        raise BlobProbeUnavailable(
            f"Several containers match {_APP_SERVICE_SUFFIX!r} ({', '.join(sorted(matches))}), "
            "so the probe cannot tell which stack to inspect. Set "
            f"{_APP_CONTAINER_ENV} to the one under test."
        )

    def _local_files_under(self, relative_path: str) -> list[str]:
        base = f"{_LOCAL_MOUNT_ROOT}/{_LOCAL_MOUNT_NAME}"
        target = f"{base}/{relative_path}".rstrip("/")
        # The missing-directory case exits with its own code, so it can be told
        # apart from a permission or mount failure. Collapsing the two would let
        # a probe that never ran report "no files", which is the pass condition
        # for assert_blobs_gone.
        script = (
            f"if [ ! -d '{target}' ]; then exit {_MISSING_DIRECTORY_EXIT}; fi; "
            f"find '{target}' -type f"
        )
        result = self._run_docker(["exec", "-i", self.container(), "sh", "-c", script])

        if result.returncode == _MISSING_DIRECTORY_EXIT:
            return []
        if result.returncode != 0:
            raise BlobProbeUnavailable(
                f"Could not list blob storage under {relative_path!r} "
                f"(exit {result.returncode}): "
                f"{(result.stderr or result.stdout)[:300]}"
            )
        return [line for line in result.stdout.splitlines() if line.strip()]

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
