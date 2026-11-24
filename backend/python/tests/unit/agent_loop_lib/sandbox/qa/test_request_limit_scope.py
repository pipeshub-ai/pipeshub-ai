"""The per-request slot limit applies where a manager serves one request.

The limit counts per ``SandboxManager``. The chat path builds one manager per
request (shared by its sub-agents), so there it bounds one chat. The library's
``ControlPlane`` shares one manager across every request of the server, so
the limit there would cap the whole server at ``SANDBOX_MAX_PER_REQUEST``
runs at once; it is opt-in and that manager does not opt in.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.control_plane.config import (
    CodingSandboxConfig,
    ControlPlaneConfig,
    GovernorConfig,
)
from app.agent_loop_lib.control_plane.control_plane import ControlPlane
from app.agent_loop_lib.sandbox.governor import (
    GovernorLimits,
    SandboxResourceGovernor,
    reset_default_governor,
)
from app.agent_loop_lib.sandbox.manager import SandboxManager, SandboxType

if TYPE_CHECKING:
    from pathlib import Path


class _PerCall:
    class capabilities:
        is_metered = False

    def __init__(self) -> None:
        self.sandbox_id = f"pc-{id(self)}"

    async def provision(self) -> None:
        await asyncio.sleep(0)

    async def destroy(self) -> None:
        await asyncio.sleep(0)


async def _peak(manager: SandboxManager, calls: int) -> int:
    """Run *calls* parallel calls on one sandbox of *manager*; the most that
    held a slot at once."""
    manager.register_backend_factory(SandboxType.CODING, _PerCall)
    sid, _ = await manager.get_or_create(SandboxType.CODING)
    running = peak = 0
    release = asyncio.Event()

    async def call() -> None:
        nonlocal running, peak
        async with manager.slot(SandboxType.CODING, sid):
            running += 1
            peak = max(peak, running)
            await release.wait()
            running -= 1

    tasks = [asyncio.create_task(call()) for _ in range(calls)]
    for _ in range(50):
        await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*tasks)
    return peak


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # private slot files
    monkeypatch.delenv("SANDBOX_STATE_DIR", raising=False)
    monkeypatch.setenv("SANDBOX_MAX_PER_REQUEST", "2")
    reset_default_governor()
    yield
    reset_default_governor()


async def test_a_manager_without_the_option_has_no_per_request_limit() -> None:
    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=8))
    manager = SandboxManager(governor=governor, slot_wait_s=5.0)
    assert manager.request_slot_limit() is None
    assert await _peak(manager, 6) == 6
    assert governor.held_total() == 0


async def test_the_chat_path_limits_each_request(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.agent_loop.sandbox_bridge import build_coding_sandbox_manager

    monkeypatch.setenv("SANDBOX_MODE", "local")
    monkeypatch.setenv("SANDBOX_ALLOW_LOCAL", "true")
    monkeypatch.setenv("SANDBOX_MAX_TOTAL", "8")
    monkeypatch.setenv("SANDBOX_SLOT_WAIT_S", "5")
    chat = await build_coding_sandbox_manager()
    assert chat.request_slot_limit() == 2
    monkeypatch.setenv("SANDBOX_MAX_PER_REQUEST", "3")  # read on each call
    assert chat.request_slot_limit() == 3
    monkeypatch.setenv("SANDBOX_MAX_PER_REQUEST", "2")
    # Its parallel tool calls and sub-agents share the one manager.
    assert await _peak(chat, 6) == 2
    # Another chat request has its own.
    other = await build_coding_sandbox_manager()
    assert other is not chat and other.request_slot_limit() == 2


async def test_the_control_plane_server_is_not_capped_per_request(tmp_path: Path) -> None:
    cfg = ControlPlaneConfig(
        tools=[], hooks=[],
        coding_sandbox=CodingSandboxConfig(
            enabled=True, backend="local", local={"working_dir_root": str(tmp_path)},
            governor=GovernorConfig(max_total_sandboxes=8),
        ),
    )
    plane = ControlPlane(cfg)
    await plane.start()
    try:
        manager = plane.sandbox_manager
        assert manager is not None
        assert manager.request_slot_limit() is None
        # Six requests of the server, each with one call, run at once: only
        # the governor's caps apply.
        assert await _peak(manager, 6) == 6
    finally:
        await plane.stop()
