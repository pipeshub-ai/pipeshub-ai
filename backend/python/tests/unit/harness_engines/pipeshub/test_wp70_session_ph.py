"""WP70-SESSIONPH: the worker, not the image, owns ph's version.

A fix to ``ph tools search`` could not reach a sandbox until the engine images were rebuilt and
re-signed. The session folder the worker writes each turn (read-only to the sandbox) now holds this worker's ph at
``/pipeshub/session/bin/ph``, first on the engine's PATH, and a login profile in the engine's home puts it first again
for login shells (Codex runs ``bash -lc``; the images' ``/etc/profile`` resets PATH).
"""

from __future__ import annotations

import io
import subprocess
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from harness_core.engines.tool_folder import _PROFILE_SCRIPT, DEFAULT_PATH
from harness_core.sandbox.tar import CONFIG_MODE, EXEC_MODE, config_tar
from harness_core.testing.engines.contract import ENDPOINTS
from harness_engines.pipeshub import (
    SESSION_PH,
    SESSION_PH_PATH,
    pipeshub_tool_folder,
    session_files,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

PH = Path(__file__).resolve().parents[4] / "harness_engines" / "pipeshub" / "ph.py"


async def _bytes(stream: AsyncIterator[bytes]) -> bytes:
    return b"".join([chunk async for chunk in stream])


async def test_wp70_sessionph_the_session_folder_holds_this_workers_ph_runnable_and_read_only() -> None:
    files = session_files((), ENDPOINTS)
    assert files[SESSION_PH] == PH.read_bytes()
    folder = pipeshub_tool_folder()
    assert folder.bin_path() == "/pipeshub/session/bin" and SESSION_PH_PATH == "/pipeshub/session/bin/ph"
    archive = await _bytes(config_tar(files, executables=folder.executables))
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        modes = {m.name: m.mode for m in tar.getmembers() if m.isfile()}
    assert modes[SESSION_PH] == EXEC_MODE and modes["gateway.json"] == CONFIG_MODE
    assert EXEC_MODE & 0o222 == 0, "never writable by the sandbox user"


@dataclass
class _Process:
    out: bytes
    code: int = 0

    def __post_init__(self) -> None:
        async def stdout() -> AsyncIterator[bytes]:
            yield self.out

        async def stderr() -> AsyncIterator[bytes]:
            if False:
                yield b""

        self.stdout, self.stderr = stdout(), stderr()

    async def wait(self, timeout_s: float | None = None) -> int:
        del timeout_s
        return self.code


@dataclass
class _Sandbox:
    """Runs what the folder asks for in a real local ``sh``, in a temporary HOME."""

    home: Path
    calls: list[tuple[list[str], dict[str, str]]] = field(default_factory=list[tuple[list[str], dict[str, str]]])

    async def exec(self, argv: list[str], *, env: Mapping[str, str]) -> _Process:
        self.calls.append((argv, dict(env)))
        done = subprocess.run(argv, env={"PATH": "/usr/bin:/bin", **env, "HOME": str(self.home)}, capture_output=True, check=False)
        return _Process(done.stdout, done.returncode)


async def test_wp70_sessionph_path_puts_the_session_ph_first_in_shells_and_login_shells(tmp_path: Path) -> None:
    sandbox = _Sandbox(tmp_path)
    env = await pipeshub_tool_folder().path_env(sandbox, {"HOME": "/tmp"})  # type: ignore[arg-type]
    assert env == {"PATH": "/pipeshub/session/bin:/usr/bin:/bin"}
    (argv, sent), = sandbox.calls
    assert argv[:3] == ["sh", "-c", _PROFILE_SCRIPT] and sent == {"HOME": "/tmp"}
    # A login shell that resets PATH (as /etc/profile does) still finds the session folder first, once.
    for profile in (".profile", ".bash_profile"):
        assert (tmp_path / profile).exists()
    login = subprocess.run(
        ["sh", "-c", f'PATH=/usr/bin:/bin; . "{tmp_path}/.profile"; . "{tmp_path}/.profile"; printf %s "$PATH"'],
        capture_output=True, text=True, check=True,
    )
    assert login.stdout == "/pipeshub/session/bin:/usr/bin:/bin"


async def test_wp70_sessionph_an_unreadable_image_path_falls_back_to_the_usual_one(tmp_path: Path) -> None:
    class Broken(_Sandbox):
        async def exec(self, argv: list[str], *, env: Mapping[str, str]) -> _Process:
            return _Process(b"", 1)

    env = await pipeshub_tool_folder().path_env(Broken(tmp_path), {})  # type: ignore[arg-type]
    assert env == {"PATH": f"/pipeshub/session/bin:{DEFAULT_PATH}"}
