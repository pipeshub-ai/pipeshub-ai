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


def test_opt_out_is_set_and_an_explicit_operator_value_is_kept() -> None:
    from app.utils.process_hardening import disable_vendor_telemetry

    unset: dict[str, str] = {}
    disable_vendor_telemetry(unset)
    assert unset == {"ORT_DISABLE_TELEMETRY": "1"}

    operator_choice = {"ORT_DISABLE_TELEMETRY": "0"}
    disable_vendor_telemetry(operator_choice)
    assert operator_choice == {"ORT_DISABLE_TELEMETRY": "0"}


def _run_clean(code: str, home: Path) -> subprocess.CompletedProcess[str]:
    # A minimal environment: a CI variable or an inherited ORT_DISABLE_TELEMETRY would also silence onnxruntime and
    # let the opt-out under test pass without doing anything.
    backend_python = Path(__file__).resolve().parents[3]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(backend_python),
        "HOME": str(home),
        "XDG_CACHE_HOME": str(home / ".cache"),
    }
    home.mkdir()
    return subprocess.run(
        [sys.executable, "-B", "-c", code], env=env, cwd=backend_python,
        capture_output=True, text=True, check=True, timeout=120,
    )


def test_importing_app_opts_the_process_out(tmp_path: Path) -> None:
    result = _run_clean("import os, app; print(os.environ.get('ORT_DISABLE_TELEMETRY'))", tmp_path / "home")
    assert result.stdout.strip() == "1"


def _files_under(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file()]


def test_onnxruntime_starts_no_telemetry_client_after_app_import(tmp_path: Path) -> None:
    pytest.importorskip("onnxruntime")
    control = tmp_path / "control"
    _ = _run_clean("import onnxruntime", control)
    # The 1DS client persists its device id and offline event queue under the cache dir as soon as it starts.
    assert _files_under(control), "onnxruntime no longer starts its telemetry client on import; revisit the opt-out in app.utils.process_hardening"

    service = tmp_path / "service"
    _ = _run_clean("import app, onnxruntime", service)
    assert _files_under(service) == []
