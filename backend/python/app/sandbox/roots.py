"""Where run_code's executors keep each run's files: one private folder per executor and per OS user.

A fixed name under the system temp folder (``/tmp/pipeshub_sandbox_docker``) is shared by every user of the
machine, and whoever creates it first owns it: they can block it, plant a symlink to another place, or swap a run's
files in a group-writable folder before PipesHub reads them back as artifacts. Each root is therefore the running
user's own (``<tempdir>/<name>-<uid>``, or ``<PIPESHUB_SANDBOX_ROOT>/<name>`` when the operator names a folder), made
0700, and used only when it is a real directory (not a link) that this user owns and nobody else can write.
"""

from __future__ import annotations

import os
import tempfile
from typing import Final

from app.utils.private_dirs import PrivateDirUnsafe
from app.utils.private_dirs import ensure_private_dir as private_dir

SANDBOX_ROOT_ENV: Final = "PIPESHUB_SANDBOX_ROOT"

LOCAL: Final = "pipeshub_sandbox"
DOCKER: Final = "pipeshub_sandbox_docker"
BROKER: Final = "pipeshub_sandbox_broker"
NAMES: Final = (LOCAL, DOCKER, BROKER)


class SandboxRootUnsafe(PrivateDirUnsafe):
    """A sandbox folder that is not this user's private directory; nothing is written to it."""


def _uid() -> int:
    return os.getuid()


def operator_root() -> str | None:
    """The folder the operator named for the roots (``PIPESHUB_SANDBOX_ROOT``); ``None``: the temp folder."""
    return _base()


def _base() -> str | None:
    configured = os.environ.get(SANDBOX_ROOT_ENV, "").strip()
    return os.path.abspath(configured) if configured else None


def sandbox_root(name: str) -> str:
    """The folder executor ``name`` keeps its runs in (not created; ``ensure_private_dir`` makes and checks it)."""
    base = _base()
    if base is not None:
        return os.path.join(base, name)
    return os.path.join(tempfile.gettempdir(), f"{name}-{_uid()}")


def sandbox_roots() -> tuple[str, ...]:
    """Every executor's root, resolved: the only places an artifact may be read from."""
    return tuple(os.path.realpath(sandbox_root(name)) for name in NAMES)


_HINT = f" Remove it if it is yours, or set {SANDBOX_ROOT_ENV} to a private folder."


def ensure_private_dir(path: str) -> str:
    """Makes ``path`` (and an operator root above it) 0700 when missing, refuses it unless it is this user's private
    directory (``SandboxRootUnsafe``), and answers its resolved path. A folder someone else made is never changed."""
    base = _base()
    chain = [base, path] if base is not None and os.path.dirname(path) == base else [path]
    try:
        for folder in chain:
            resolved = private_dir(folder, "sandbox folder", _HINT)
    except PrivateDirUnsafe as unsafe:
        raise SandboxRootUnsafe(str(unsafe)) from None
    return resolved


__all__ = [
    "BROKER",
    "DOCKER",
    "LOCAL",
    "NAMES",
    "SANDBOX_ROOT_ENV",
    "SandboxRootUnsafe",
    "ensure_private_dir",
    "operator_root",
    "sandbox_root",
    "sandbox_roots",
]
