"""Package specs reach the Docker install command as single, literal words.

The install runs as ``sh -c "<pip|npm> install ... <specs>"``. A version
range such as ``mpmath<1.0`` or ``numpy>=1.26`` passes spec validation, but
unquoted the shell reads ``<`` and ``>`` as redirections: the first fails
with "cannot open 1.0", the second installs any version and writes the
installer's output to a file named ``=1.26``. A ``*`` in an npm range would
be glob-expanded. The script is run here by a real ``sh`` with fake
``pip``/``npm``/``mkdir`` that print the words they were given.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")

_FAKE_TOOL = '#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n'


def _install_script(tmp_path: Path, specs: list[str], language: str) -> str:
    sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
    captured: dict[str, Any] = {}

    def fake_install(client, network, extract_path, host_target, to_install, command_kwargs, handle=None) -> tuple[int, str, str]:
        captured["command"] = command_kwargs["command"]
        return 0, "", ""

    sandbox._firewalled = lambda command, cidrs, token: {"command": command}  # type: ignore[method-assign]
    sandbox._install_in_container = fake_install  # type: ignore[method-assign]
    sandbox._install_packages_sync(specs, language, client=None, network_name="egress", egress_cidrs=["10.9.0.0/16"])
    # deadline_command(["sh", "-c", script], s) keeps the script as its last word.
    return captured["command"][-1]


def _run(script: str, tmp_path: Path) -> tuple[list[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("pip", "npm", "mkdir"):
        path = bin_dir / tool
        path.write_text(_FAKE_TOOL)
        path.chmod(0o755)
    work = tmp_path / "cwd"
    work.mkdir()
    (work / "lodash@4.17.21").write_text("")  # a glob target for `lodash@4.*`
    result = subprocess.run(
        ["sh", "-c", script], cwd=work, capture_output=True, text=True, timeout=30,
        env={"PATH": f"{bin_dir}{os.pathsep}/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines(), sorted(p.name for p in work.iterdir())


def test_python_version_ranges_are_passed_to_pip_unchanged(tmp_path: Path) -> None:
    specs = ["mpmath<1.0", "numpy>=1.26", "pandas!=2.0"]
    words, files = _run(_install_script(tmp_path, specs, "python"), tmp_path)
    for spec in specs:
        assert spec in words
    assert files == ["lodash@4.17.21"], "no file may be created by a redirection"


def test_npm_ranges_are_not_glob_expanded(tmp_path: Path) -> None:
    words, files = _run(_install_script(tmp_path, ["lodash@4.*"], "typescript"), tmp_path)
    assert "lodash@4.*" in words
    assert "lodash@4.17.21" not in words
    assert files == ["lodash@4.17.21"]
