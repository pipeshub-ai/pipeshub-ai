"""Tests for app.utils.process_hardening."""
import os
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

_DRIVER = textwrap.dedent(
    """
    import subprocess, sys
    from app.utils.process_hardening import mark_process_non_dumpable

    if sys.argv[1] == "harden":
        assert mark_process_non_dumpable()
    result = subprocess.run(
        ["/bin/sh", "-c", "tr '\\\\0' '\\\\n' < /proc/$PPID/environ"], capture_output=True, text=True,
    )
    sys.stdout.write(result.stdout)
    """
)


def _child_view_of_parent_env(mode: str, secret: str) -> str:
    backend_python = Path(__file__).resolve().parents[3]
    env = {**os.environ, "PIPESHUB_FAKE_SECRET_KEY": secret, "PYTHONPATH": str(backend_python)}
    return subprocess.run(
        [sys.executable, "-c", _DRIVER, mode], env=env, cwd=backend_python,
        capture_output=True, text=True, check=True, timeout=30,
    ).stdout


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="prctl is Linux-only")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root bypasses the dumpable check")
def test_same_uid_child_cannot_read_hardened_parent_environ() -> None:
    secret = f"s3cr3t-{uuid.uuid4().hex}"
    assert secret in _child_view_of_parent_env("plain", secret)
    assert secret not in _child_view_of_parent_env("harden", secret)


def test_non_linux_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.utils import process_hardening

    monkeypatch.setattr(process_hardening.sys, "platform", "darwin")
    assert process_hardening.mark_process_non_dumpable() is False
