"""RT-1, RT-2 (SK1): the skill-runtime test variant's lock and build script.

``build.sh`` runs with a stub ``docker`` on PATH that answers ``image inspect`` with a chosen binary-digest
label and records ``docker build``'s argv and context: a Codex image that does not carry the pinned digest
must stop the build before ``docker build``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[5]
HERE = REPO / "deployment" / "sandbox-images" / "skill-runtime"
ALLOWLIST = json.loads((REPO / "backend" / "python" / "harness_engines" / "allowlist.json").read_text())
CODEX = next(e for e in ALLOWLIST["engines"] if e["kind"] == "codex")
OPENCODE = next(e for e in ALLOWLIST["engines"] if e["kind"] == "opencode")
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==(\S+) \\$")


def test_rt1_the_lock_pins_exactly_the_skill_packages_by_hash() -> None:
    lines = [line for line in (HERE / "skills.lock").read_text().splitlines() if line and not line.startswith("#")]
    pins = [PIN.match(line) for line in lines[::2]]
    assert all(pins), lines
    assert {m.group(1) for m in pins if m} == {"defusedxml"}
    assert all(re.fullmatch(r" {4}--hash=sha256:[0-9a-f]{64}", line) for line in lines[1::2]), lines
    # Read ahead of the tools lock by runtime_note.py: a dependencies header here would demote the tools lock's
    # top-level packages in the image's RUNTIME.md.
    assert not any(line.startswith("# Dependencies") for line in (HERE / "skills.lock").read_text().splitlines())


def _build(tmp_path: Path, target: str, label: str) -> tuple[int, str, list[str], list[str]]:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    argv_file, context_file = tmp_path / "argv", tmp_path / "context"
    (stub / "docker").write_text(
        "#!/bin/sh\n"
        'if [ "$1" = image ] && [ "$2" = inspect ]; then printf "%s\\n" "$STUB_LABEL"; exit 0; fi\n'
        f'printf "%s\\n" "$@" > {argv_file}\n'
        f'for last; do :; done\nls "$last" > {context_file}\n'
    )
    (stub / "docker").chmod(0o755)
    env = {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}", "STUB_LABEL": label}
    done = subprocess.run(
        ["sh", str(HERE / "build.sh"), target], capture_output=True, text=True, env=env, check=False, timeout=60
    )
    argv = argv_file.read_text().splitlines() if argv_file.exists() else []
    context = context_file.read_text().splitlines() if context_file.exists() else []
    return done.returncode, done.stderr, argv, context


@pytest.mark.parametrize("target", ["opencode", "codex"])
def test_rt2_builds_the_target_from_the_pinned_bases(tmp_path: Path, target: str) -> None:
    code, err, argv, context = _build(tmp_path, target, CODEX["binaryDigest"])
    assert code == 0, err
    assert argv[:3] == ["build", "--target", target]
    assert f"TOOLS_IMAGE={OPENCODE['variants']['tools']}" in argv
    assert f"CODEX_IMAGE={CODEX['image']}" in argv
    assert f"CODEX_SHA256={CODEX['binaryDigest'].removeprefix('sha256:')}" in argv
    assert argv[-2:] == ["-t", f"pxh-sk1-{target}:test"] or argv[-3:-1] == ["-t", f"pxh-sk1-{target}:test"]
    assert {"Dockerfile", "skills.lock", "requirements.lock", "runtime_note.py", "node-package-lock.json", "licenses"} <= set(
        context
    )


def test_rt2_a_codex_image_with_another_binary_stops_before_docker_build(tmp_path: Path) -> None:
    code, err, argv, _ = _build(tmp_path, "codex", "sha256:" + "0" * 64)
    assert code != 0
    assert "the allowlist pins" in err
    assert argv == []


def test_rt2_an_unknown_target_is_a_usage_error(tmp_path: Path) -> None:
    code, err, argv, _ = _build(tmp_path, "claude", CODEX["binaryDigest"])
    assert (code, argv) == (2, [])
    assert "usage" in err
