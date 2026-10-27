"""Tests run with tracing to a real Opik account switched off (see `tests/conftest.py`)."""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

import dotenv

from app.agent_loop_lib.transport.opik_tracing import is_opik_configured

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_the_tracing_gate_is_closed() -> None:
    assert not is_opik_configured()


def test_the_opik_sdk_has_no_account_to_send_to() -> None:
    from opik.config import OpikConfig

    config = OpikConfig()
    assert not config.api_key
    assert not config.workspace or config.workspace == "default"


def test_a_dotenv_file_cannot_switch_it_back_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPIK_API_KEY", "")
    monkeypatch.setenv("OPIK_WORKSPACE", "")
    env_file = tmp_path / ".env"
    env_file.write_text("OPIK_API_KEY=from-a-dotenv\nOPIK_WORKSPACE=someone\n", encoding="utf-8")

    dotenv.load_dotenv(env_file)

    assert os.environ["OPIK_API_KEY"] == ""
    assert not is_opik_configured()


_IMPORT_PROBE = """
import httpx, sys
import opik
sent = []
for client in (httpx.Client, httpx.AsyncClient):
    client.send = lambda self, *a, **k: sent.append(a) or (_ for _ in ()).throw(AssertionError("request sent"))
created = []
real_init = opik.Opik.__init__
opik.Opik.__init__ = lambda self, *a, **k: created.append(1) or real_init(self, *a, **k)

import app.agent_loop_lib.transport.opik_tracing as tracing
import app.utils.opik as helpers

assert not tracing.is_opik_configured()
assert helpers.get_opik_tracer() is None
assert tracing.build_langchain_opik_callbacks() == []
print("clients", len(created), "requests", len(sent))
"""


def test_importing_the_tracing_modules_creates_no_client_and_sends_nothing() -> None:
    import subprocess
    import sys
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    # The session environment already carries the blank Opik settings from tests/conftest.py.
    env = {**os.environ, "PYTHONPATH": str(backend)}

    result = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", _IMPORT_PROBE], env=env, cwd=backend,
        capture_output=True, text=True, timeout=300, check=False,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert result.stdout.strip().endswith("clients 0 requests 0")


def test_the_tracing_helpers_do_nothing_without_config(monkeypatch: pytest.MonkeyPatch) -> None:

    import opik

    from app.agent_loop_lib.transport.opik_tracing import (
        build_langchain_opik_callbacks,
        resolve_opik_gate,
        wrap_if_enabled,
    )
    from app.utils.opik import get_llm_invoke_config

    def _refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an Opik client was created")

    monkeypatch.setattr(opik.Opik, "__init__", _refuse)
    monkeypatch.setattr(opik, "get_global_client", _refuse)
    transport = object()

    assert resolve_opik_gate(True) is False
    assert wrap_if_enabled(transport, enabled=resolve_opik_gate(True)) is transport  # type: ignore[arg-type]
    assert build_langchain_opik_callbacks() == []
    assert get_llm_invoke_config() == {}
