"""One alert evaluator per state directory: whoever holds ``load/leader``."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from app.services.resource_governor.shared_slots import (
    ensure_private_directory,
    open_private_file,
)

if TYPE_CHECKING:
    from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class AlertLeader:
    """A non-blocking ``flock`` on ``<state dir>/load/leader``. The kernel drops
    it when the holder exits, however it exits, so another worker takes over
    at its next try."""

    def __init__(self, state_directory: Path) -> None:
        self._directory = state_directory / "load"
        self._state_directory = state_directory
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def try_acquire(self) -> bool:
        """Whether this process holds the lock (now or already). Raises
        ``OSError`` when the directory cannot be used."""
        if self._fd is not None:
            return True
        if fcntl is None:
            raise OSError("no POSIX file locks")
        ensure_private_directory(self._state_directory)
        ensure_private_directory(self._directory)
        fd = open_private_file(self._directory / "leader")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        logger.info("sandbox load alerts: this worker (pid %d) evaluates", os.getpid())
        return True

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)
