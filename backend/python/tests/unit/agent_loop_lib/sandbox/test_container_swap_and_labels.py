"""Sandbox containers get no swap and carry the app's labels.

The Docker proxy forces ``MemorySwap = Memory``; with the socket mounted
straight from the daemon the app must ask for it itself, or Docker allows as
much swap again as memory. The labels let an operator find containers a
killed worker left behind when no proxy reaps them.
"""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest

import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
from app.agent_loop_lib.sandbox.coding.base import CodeRequest
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox

from .test_docker_coding_sandbox import (  # noqa: F401  (fixtures)
    _fake_container,
    _fake_docker_module,
    _fresh_docker_provider,
)

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_DISK_QUOTA", "off")
    monkeypatch.setattr(ef, "_oom_score_adj_usable", True)


class TestCreateSandboxContainer:
    def test_swap_equals_memory(self) -> None:
        client = MagicMock()
        ef.create_sandbox_container(client, image="img", mem_limit=512 * MB, detach=True)
        kwargs = client.containers.create.call_args.kwargs
        assert kwargs["memswap_limit"] == kwargs["mem_limit"] == 512 * MB

    def test_labels_name_the_purpose_and_the_state_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_STATE_ID", "stack-a")
        client = MagicMock()
        ef.create_sandbox_container(client, image="img", mem_limit=MB)
        ef.create_sandbox_container(client, purpose="install", image="img", mem_limit=MB)
        run, install = (c.kwargs["labels"] for c in client.containers.create.call_args_list)
        assert run == {"ai.pipeshub.sandbox": "run", "ai.pipeshub.sandbox.state-id": "stack-a"}
        assert install["ai.pipeshub.sandbox"] == "install"
        assert "purpose" not in client.containers.create.call_args.kwargs

    def test_no_state_id_label_without_a_valid_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SANDBOX_STATE_ID", "bad id!")
        client = MagicMock()
        ef.create_sandbox_container(client, image="img", mem_limit=MB)
        assert client.containers.create.call_args.kwargs["labels"] == {"ai.pipeshub.sandbox": "run"}

    def test_the_oom_score_fallback_keeps_swap_and_labels(self) -> None:
        client = MagicMock()
        ok = MagicMock()
        client.containers.create.side_effect = [
            RuntimeError("HostConfig field 'OomScoreAdj' is not permitted"), ok,
        ]
        assert ef.create_sandbox_container(client, image="img", mem_limit=MB) is ok
        kwargs = client.containers.create.call_args.kwargs
        assert "oom_score_adj" not in kwargs
        assert kwargs["memswap_limit"] == MB
        assert kwargs["labels"]["ai.pipeshub.sandbox"] == "run"


class TestDockerCodingSandbox:
    async def test_a_run_container_has_no_swap_and_the_run_label(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        fake_client = MagicMock()
        fake_client.containers.create.return_value = _fake_container(exit_code=0)
        with patch("docker.from_env", return_value=fake_client):
            await sandbox.execute(CodeRequest(code="print(1)", language="python"))
        kwargs = fake_client.containers.create.call_args.kwargs
        assert kwargs["memswap_limit"] == kwargs["mem_limit"]
        assert kwargs["labels"]["ai.pipeshub.sandbox"] == "run"

    def test_an_install_container_has_no_swap_and_the_install_label(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        client = MagicMock()
        client.containers.create.return_value = _fake_container(exit_code=0)
        with contextlib.suppress(Exception):
            sandbox._install_in_container(
                client, "egress", "/deps", str(tmp_path / "deps"), ["mpmath"], {"command": ["true"]},
            )
        kwargs = client.containers.create.call_args.kwargs
        assert kwargs["memswap_limit"] == kwargs["mem_limit"]
        assert kwargs["labels"]["ai.pipeshub.sandbox"] == "install"
