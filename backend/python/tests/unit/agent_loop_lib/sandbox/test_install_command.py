"""The Python install command (P5.11 RC-11): what the image has is not reinstalled, pip stages off ``/tmp``.

``pip install --target /deps pandas`` in a sandbox whose ``/tmp`` is a 100 MB tmpfs failed with ENOSPC
although the image ships pandas. The command is run here with the test interpreter as the image's
``python3`` (it has ``pytest``, as the image has pandas) and a ``pip`` that records its arguments, with
``/deps`` mapped to a temporary directory.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox

if TYPE_CHECKING:
    from pathlib import Path

INDEX = "https://index.example/simple"


def _command(to_install: list[str], tmp_path: Path) -> list[str]:
    sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), image="img", pip_index_url=INDEX)
    cmd, extract, _host = sandbox._install_command(to_install, "python")  # pyright: ignore[reportPrivateUsage]
    assert extract == "/deps"
    return cmd


def _shim(tmp_path: Path) -> Path:
    shim = tmp_path / "bin"
    shim.mkdir(exist_ok=True)
    # A wrapper, not a symlink: a venv interpreter finds its site-packages from its own path.
    (shim / "python3").write_text(f'#!/bin/sh\nexec {sys.executable} "$@"\n')
    (shim / "python3").chmod(0o755)
    return shim


def _run(cmd: list[str], tmp_path: Path) -> tuple[int, list[str], bool]:
    deps = tmp_path / "deps"
    deps.mkdir(exist_ok=True)
    shim = _shim(tmp_path)
    log = tmp_path / "pip.log"
    pip = shim / "pip"
    pip.write_text(f'#!/bin/sh\necho "$@" > {log}\necho "$TMPDIR" >> {log}\nls -d "$TMPDIR" >> {log}\n')
    pip.chmod(pip.stat().st_mode | stat.S_IEXEC)
    script = cmd[2].replace("/deps", str(deps))
    env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}"}
    done = subprocess.run([cmd[0], cmd[1], script, *cmd[3:]], env=env, capture_output=True, text=True, check=False)
    lines = log.read_text().splitlines() if log.exists() else []
    return done.returncode, lines, (deps / ".pip-tmp").exists()


def test_rc11_a_requirement_the_image_has_is_not_installed(tmp_path: Path) -> None:
    code, pip_calls, leftover = _run(_command(["pytest"], tmp_path), tmp_path)
    assert (code, pip_calls, leftover) == (0, [], False)


def test_rc11_only_the_missing_requirements_reach_pip_staged_on_the_install_mount(tmp_path: Path) -> None:
    code, lines, leftover = _run(_command(["pytest", "no-such-dist-p511", "pytest==1.0"], tmp_path), tmp_path)
    assert code == 0
    args, tmpdir, listed = lines
    assert args == f"install --quiet --no-cache-dir --target {tmp_path}/deps --index-url {INDEX} no-such-dist-p511 pytest==1.0"
    # pip's staging is under the install mount (not the /tmp tmpfs) and gone afterwards.
    assert tmpdir == listed == f"{tmp_path}/deps/.pip-tmp"
    assert not leftover


def test_rc11_a_failed_install_fails_the_command(tmp_path: Path) -> None:
    cmd = _command(["no-such-dist-p511"], tmp_path)
    shim = _shim(tmp_path)
    (shim / "pip").write_text("#!/bin/sh\nexit 7\n")
    (shim / "pip").chmod(0o755)
    env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}"}
    script = cmd[2].replace("/deps", str(tmp_path / "deps"))
    (tmp_path / "deps").mkdir()
    done = subprocess.run([cmd[0], cmd[1], script, *cmd[3:]], env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 7
    assert not (tmp_path / "deps" / ".pip-tmp").exists()


def test_rc11_typescript_install_is_unchanged(tmp_path: Path) -> None:
    sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"), image="img", npm_registry="https://npm.example")
    cmd, extract, _host = sandbox._install_command(["lodash"], "typescript")  # pyright: ignore[reportPrivateUsage]
    assert extract == "/install/node_modules"
    assert cmd == [
        "sh",
        "-c",
        "mkdir -p /install && npm install --prefix /install --no-save --loglevel=error "
        "--registry=https://npm.example lodash",
    ]
