"""``tests/conftest.py`` keeps a checkout's `.env` code sandbox and Docker proxy
settings out of the tests, so their outcome does not depend on a developer's
local stack."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import dotenv

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_sandbox_and_proxy_settings_from_a_dotenv_file_are_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "SANDBOX_DOCKER_IMAGE=someone/local:dev\nDOCKER_PROXY_INSTANCE=mine\n"
        "PIPESHUB_TEST_DOTENV_OTHER=kept\n",
    )
    for key in ("SANDBOX_DOCKER_IMAGE", "DOCKER_PROXY_INSTANCE", "PIPESHUB_TEST_DOTENV_OTHER"):
        monkeypatch.delenv(key, raising=False)
    try:
        dotenv.load_dotenv(env_file)
        assert "SANDBOX_DOCKER_IMAGE" not in os.environ
        assert "DOCKER_PROXY_INSTANCE" not in os.environ
        assert os.environ["PIPESHUB_TEST_DOTENV_OTHER"] == "kept"
    finally:
        os.environ.pop("PIPESHUB_TEST_DOTENV_OTHER", None)


def test_values_set_in_the_shell_are_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("SANDBOX_TEST_IMAGE=from-dotenv\n")
    monkeypatch.setenv("SANDBOX_TEST_IMAGE", "from-shell")
    dotenv.load_dotenv(env_file)
    assert os.environ["SANDBOX_TEST_IMAGE"] == "from-shell"
