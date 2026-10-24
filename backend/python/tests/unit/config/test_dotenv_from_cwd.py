"""Services read the .env of the directory they are started from."""

from __future__ import annotations

import os
import runpy

import pytest


@pytest.fixture
def env_in_cwd(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("PIPESHUB_DOTENV_PROBE=from-cwd\n", encoding="utf-8")
    (tmp_path / "logs").mkdir()  # loggers created on import write to ./logs
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PIPESHUB_DOTENV_PROBE", raising=False)
    yield
    os.environ.pop("PIPESHUB_DOTENV_PROBE", None)


# run_module executes a fresh copy without replacing the cached module: a reload
# would leave other tests holding a ConfigurationService class isinstance rejects.
def test_configuration_service_loads_the_working_directory_env(env_in_cwd) -> None:
    runpy.run_module("app.config.configuration_service")

    assert os.environ.get("PIPESHUB_DOTENV_PROBE") == "from-cwd"


def test_a_service_container_loads_the_working_directory_env(env_in_cwd) -> None:
    runpy.run_module("app.containers.parsing")

    assert os.environ.get("PIPESHUB_DOTENV_PROBE") == "from-cwd"
