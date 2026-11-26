"""Text files of a sandbox's workspace, read and written for a client protocol (ACP's ``fs/*``), never outside it.

Two checks guard every path. The first is here, before anything runs: the path must be absolute, already
normalised, free of NUL and control characters, and under the workspace root. The second runs in the sandbox as the
sandbox user (``READ_SCRIPT``, ``WRITE_SCRIPT``, through ``SandboxHandle.exec``), so a symbolic link cannot carry the
path out: the file a read resolves to, and the folder a write lands in, must still be under the root once every link
is followed. The scripts use only POSIX ``sh``, ``readlink -f``, ``dirname``, ``basename``, ``mkdir``, ``head`` and
``cat``, which both the Alpine (busybox) and the Debian (coreutils) engine images ship.

Running as the sandbox user is the reason the second check cannot be raced into more than the engine already has:
a link swapped between the check and the write lands the bytes where the engine's own process could have written
them anyway. Nothing here runs as root, and nothing is written through ``put_tar``.
"""

from __future__ import annotations

import asyncio
import contextlib
import posixpath
import re
import signal
from typing import TYPE_CHECKING, Final

from harness_core.sandbox.streams import collect

if TYPE_CHECKING:
    from harness_core.sandbox.ports import SandboxHandle

MAX_PATH_BYTES: Final = 4096
EXEC_TIMEOUT_S: Final = 30.0
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

READ_SCRIPT: Final = (
    'f=$(readlink -f "$2") || exit 3; '
    'case "$f" in "$1"/*) ;; *) exit 4;; esac; '
    '[ -e "$f" ] || exit 3; [ -f "$f" ] || exit 5; '
    'exec head -c "$3" "$f"'
)
WRITE_SCRIPT: Final = (
    'a=$(dirname "$2"); while [ ! -d "$a" ]; do a=$(dirname "$a"); done; '
    'r=$(readlink -f "$a") || exit 3; '
    'case "$r/" in "$1"/*) ;; *) exit 4;; esac; '
    'd=$(dirname "$2"); mkdir -p "$d" || exit 6; '
    'r=$(readlink -f "$d") || exit 3; '
    'case "$r/" in "$1"/*) ;; *) exit 4;; esac; '
    'f="$r/$(basename "$2")"; '
    '[ -L "$f" ] && exit 4; [ -d "$f" ] && exit 5; '
    'exec cat > "$f"'
)
# SEC-FS-02: a file the controller placed under a fresh, unguessable name ($2, directly under the root: no folder of
# its own shows up while its bytes stream in, for a running engine to see and swap) moved to its place ($3) as the
# sandbox user, with the same checks as a write. ``put_tar`` writes as root and would follow a link the engine planted
# where the file goes; the move can land only where the engine could write. The staged file goes either way.
MOVE_SCRIPT: Final = (
    's="$2"; t="$3"; '
    'd=$(dirname "$t"); mkdir -p "$d" || { rm -f "$s"; exit 6; }; '
    'r=$(readlink -f "$d") || { rm -f "$s"; exit 3; }; '
    'case "$r/" in "$1"/*) ;; *) rm -f "$s"; exit 4;; esac; '
    'f="$r/$(basename "$t")"; '
    'if [ -L "$f" ] || [ -d "$f" ]; then rm -f "$s"; exit 4; fi; '
    'mv -f "$s" "$f" || { rm -f "$s"; exit 6; }'
)
_REASONS: Final = {3: "not_found", 4: "outside_workspace", 5: "not_a_file", 6: "not_writable"}


class WorkspacePathRefused(Exception):
    """A path the workspace does not serve; ``reason_code`` names why, the message never holds the path."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(f"workspace file access refused: {reason_code}")
        self.reason_code = reason_code


def workspace_path(root: str, path: str) -> str:
    """``path`` when it names a file under ``root`` as written, before any link is followed; else refused."""
    if not path.startswith("/") or len(path.encode("utf-8", "surrogateescape")) > MAX_PATH_BYTES:
        raise WorkspacePathRefused("not_absolute")
    if _CONTROL.search(path) is not None:
        raise WorkspacePathRefused("bad_path")
    if posixpath.normpath(path) != path or path.startswith("//"):
        raise WorkspacePathRefused("not_normalised")
    if not path.startswith(root.rstrip("/") + "/"):
        raise WorkspacePathRefused("outside_workspace")
    return path


async def _run(sandbox: SandboxHandle, argv: list[str], stdin: bytes | None, *, out_limit: int) -> tuple[int, bytes]:
    process = await sandbox.exec(argv, env={}, stdin=stdin is not None)

    async def run() -> tuple[int, bytes]:
        reading = asyncio.gather(collect(process.stdout, limit=out_limit), collect(process.stderr, limit=1 << 16))
        if stdin is not None:
            with contextlib.suppress(Exception):
                await process.write(stdin)
            await process.close_stdin()
        try:
            out, _ = await reading
        except ValueError:
            raise WorkspacePathRefused("too_large") from None
        return await process.wait(timeout_s=EXEC_TIMEOUT_S), out

    try:
        return await asyncio.wait_for(run(), EXEC_TIMEOUT_S)
    except TimeoutError:
        with contextlib.suppress(Exception):
            await process.signal(signal.SIGKILL)
        raise WorkspacePathRefused("timed_out") from None


async def read_workspace_text(sandbox: SandboxHandle, root: str, path: str, *, max_bytes: int) -> bytes:
    """The file's bytes, at most ``max_bytes`` (``too_large`` past them)."""
    checked = workspace_path(root, path)
    argv = ["sh", "-c", READ_SCRIPT, "fs-read", root.rstrip("/"), checked, str(max_bytes + 1)]
    code, out = await _run(sandbox, argv, None, out_limit=max_bytes + 1)
    if code != 0:
        raise WorkspacePathRefused(_REASONS.get(code, "read_failed"))
    if len(out) > max_bytes:
        raise WorkspacePathRefused("too_large")
    return out


async def write_workspace_text(sandbox: SandboxHandle, root: str, path: str, data: bytes, *, max_bytes: int) -> None:
    """Writes ``data`` to the file, creating its folders under the root; never through a final symbolic link."""
    checked = workspace_path(root, path)
    if len(data) > max_bytes:
        raise WorkspacePathRefused("too_large")
    argv = ["sh", "-c", WRITE_SCRIPT, "fs-write", root.rstrip("/"), checked]
    code, _ = await _run(sandbox, argv, data, out_limit=1 << 16)
    if code != 0:
        raise WorkspacePathRefused(_REASONS.get(code, "write_failed"))


async def move_into_workspace(sandbox: SandboxHandle, root: str, staged: str, path: str) -> None:
    """Moves ``staged`` (a file directly under ``root``) to ``path``, as the sandbox user; the staged file goes
    either way. ``WorkspacePathRefused`` when ``path`` would land outside ``root`` or on a link."""
    argv = ["sh", "-c", MOVE_SCRIPT, "fs-move", root.rstrip("/"), workspace_path(root, staged), workspace_path(root, path)]
    code, _ = await _run(sandbox, argv, None, out_limit=1 << 16)
    if code != 0:
        raise WorkspacePathRefused(_REASONS.get(code, "move_failed"))


__all__ = [
    "MAX_PATH_BYTES",
    "MOVE_SCRIPT",
    "move_into_workspace",
    "READ_SCRIPT",
    "WRITE_SCRIPT",
    "WorkspacePathRefused",
    "read_workspace_text",
    "workspace_path",
    "write_workspace_text",
]
