from __future__ import annotations

import atexit
import logging
import shutil
import signal
import threading

"""Process-exit safety net for coding-sandbox working directories.

`CodingSandboxBackend.destroy()` is async and is the primary cleanup path
(called by `SandboxManager.destroy()`/`destroy_all()` and `ControlPlane.stop()`).
This module exists for the case where a sandbox dir is never explicitly
destroyed — an agent crash, an uncaught exception, or the process being
killed by SIGTERM/SIGINT — so temp directories (which can contain a full
`node_modules/` + `.venv/`) don't silently pile up on the host.

Deliberately synchronous: `atexit` callbacks cannot `await`, so this can
only ever do a best-effort `shutil.rmtree`, never call the real async
`destroy()` (which may involve backend-specific teardown for future remote
backends — this module is LOCAL-only cleanup).
"""

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_registered_dirs: set[str] = set()
_installed = False
_prev_sigterm = None
_prev_sigint = None


def register_sandbox_dir(path: str) -> None:
    """Track `path` for best-effort removal on process exit."""
    with _lock:
        _registered_dirs.add(path)
    _ensure_installed()


def unregister_sandbox_dir(path: str) -> None:
    """Stop tracking `path` — call this after a normal, successful `destroy()`
    so the atexit sweep doesn't redundantly try to remove an already-gone dir."""
    with _lock:
        _registered_dirs.discard(path)


def _cleanup_all() -> None:
    with _lock:
        dirs = list(_registered_dirs)
        _registered_dirs.clear()
    for path in dirs:
        try:
            shutil.rmtree(path, ignore_errors=True)
        except Exception:
            logger.warning("coding sandbox cleanup failed for %s", path, exc_info=True)


def _signal_handler(signum: int, frame: object) -> None:
    _cleanup_all()
    prev = _prev_sigterm if signum == signal.SIGTERM else _prev_sigint
    if callable(prev):
        prev(signum, frame)
    elif prev == signal.SIG_DFL:
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)


def _ensure_installed() -> None:
    """Idempotently install the atexit + SIGTERM/SIGINT hooks, chaining any
    handler already installed by the host application rather than
    clobbering it — agent-loop is a library, not the owner of the process's
    signal handling."""
    global _installed, _prev_sigterm, _prev_sigint
    with _lock:
        if _installed:
            return
        _installed = True
        atexit.register(_cleanup_all)
        try:
            _prev_sigterm = signal.getsignal(signal.SIGTERM)
            _prev_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGTERM, _signal_handler)
            signal.signal(signal.SIGINT, _signal_handler)
        except (ValueError, OSError):
            # Not in the main thread, or platform doesn't support it —
            # atexit alone still covers normal interpreter shutdown.
            logger.debug("could not install signal handlers for sandbox cleanup", exc_info=True)
