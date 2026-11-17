"""Bounded log reads never follow a container that is still running."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.sandbox.coding import docker as coding_docker
from app.sandbox import docker_executor


@pytest.mark.parametrize("capped_logs", [coding_docker._capped_logs, docker_executor._capped_logs])
def test_logs_are_read_without_following(capped_logs: object) -> None:
    container = MagicMock()
    container.logs.return_value = iter([b"out"])
    assert capped_logs(container, stdout=True) == "out"  # type: ignore[operator]
    assert container.logs.call_args.kwargs["follow"] is False
    assert container.logs.call_args.kwargs["stream"] is True
