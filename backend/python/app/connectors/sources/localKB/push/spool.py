"""Parent-owned scratch files for a push's body and stored bytes.

The body is written here by the connectors process and read by the worker; the
worker writes the stored bytes here and the upload reads them. A 16 MiB body then
never crosses a pipe, and the files of a dead worker are still owned (and
removed) by the parent. Each process run uses its own directory; ``sweep``
removes the directories of runs that ended long ago.
"""

import contextlib
import errno
import os
import shutil
import tempfile
import time
import uuid

from .contract import PushRefusal

RUN_PREFIX = "run-"


def default_root() -> str:
    return os.environ.get("PUSH_SPOOL_DIR") or os.path.join(tempfile.gettempdir(), "pipeshub-push-spool")


def sweep(root: str, max_age_s: float = 900.0, now: float | None = None) -> int:
    """Remove run directories not touched for ``max_age_s``; returns how many."""
    now = time.time() if now is None else now
    removed = 0
    if not os.path.isdir(root):
        return 0
    for entry in os.scandir(root):
        if entry.is_dir(follow_symlinks=False) and entry.name.startswith(RUN_PREFIX):
            try:
                age = now - entry.stat().st_mtime
            except OSError:
                continue
            if age > max_age_s:
                shutil.rmtree(entry.path, ignore_errors=True)
                removed += 1
    return removed


class Spool:
    def __init__(self, root: str | None = None) -> None:
        self.root = root or default_root()
        os.makedirs(self.root, mode=0o700, exist_ok=True)
        self.dir = os.path.join(self.root, f"{RUN_PREFIX}{uuid.uuid4().hex}")
        os.makedirs(self.dir, mode=0o700)

    def new_path(self, suffix: str) -> str:
        return os.path.join(self.dir, f"{uuid.uuid4().hex}.{suffix}")

    def write(self, data: bytes) -> str:
        # Another process's sweep may have removed this idle process's directory.
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        path = self.new_path("in")
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            self.remove(path)
            if e.errno in (errno.ENOSPC, errno.EDQUOT):
                raise PushRefusal(503, "SPOOL_FULL", "The server has no space to hold the request.", extra={"retryAfter": 5}) from e
            raise
        return path

    def touch(self) -> None:
        with contextlib.suppress(OSError):
            os.utime(self.dir)

    @staticmethod
    def remove(*paths: str | None) -> None:
        for p in paths:
            if p:
                with contextlib.suppress(OSError):
                    os.unlink(p)

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)
