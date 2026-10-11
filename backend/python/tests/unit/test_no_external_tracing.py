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
