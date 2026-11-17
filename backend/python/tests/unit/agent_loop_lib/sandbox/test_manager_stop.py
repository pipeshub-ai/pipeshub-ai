"""Tests for `SandboxManager.unless_stopped`: a sandbox call raced against the
run's Stop token. Stop cancels the call once, waits for its cleanup, then
raises `SandboxCallStopped`; a cancel of the calling task itself stays a
plain `CancelledError`."""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.sandbox import manager as manager_mod
from app.agent_loop_lib.sandbox.manager import (
    SandboxCallStopped,
    SandboxLimitExceeded,
    SandboxManager,
)


def _pending_token_waits() -> int:
    return sum(
        1 for t in asyncio.all_tasks()
        if not t.done() and "CancellationToken.wait" in repr(t.get_coro())
    )


async def test_without_a_token_the_call_just_runs() -> None:
    async def fn() -> str:
        return "ran"

    assert await SandboxManager().unless_stopped(fn, tool="run_code") == "ran"


async def test_a_token_never_set_returns_the_result_and_leaves_no_task() -> None:
    before = _pending_token_waits()

    async def fn() -> str:
        await asyncio.sleep(0.01)
        return "ran"

    mgr = SandboxManager(stop_token=CancellationToken())
    assert await mgr.unless_stopped(fn, tool="run_code") == "ran"
    await asyncio.sleep(0)
    assert _pending_token_waits() == before


async def test_a_token_already_set_never_starts_the_call() -> None:
    token = CancellationToken()
    token.cancel()
    called = False

    def fn() -> object:
        nonlocal called
        called = True
        raise AssertionError("must not be called")

    with pytest.raises(SandboxCallStopped):
        await SandboxManager(stop_token=token).unless_stopped(fn, tool="run_code")
    assert called is False


async def test_stop_mid_call_waits_for_its_cleanup_before_raising() -> None:
    token = CancellationToken()
    events: list[str] = []
    started = asyncio.Event()

    async def fn() -> str:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.sleep(0.2)
            events.append("cleanup done")
            raise
        return "ran"

    async def stop_later() -> None:
        await started.wait()
        token.cancel()

    stopper = asyncio.ensure_future(stop_later())
    with pytest.raises(SandboxCallStopped):
        await SandboxManager(stop_token=token).unless_stopped(fn, tool="run_code")
    events.append("stopped raised")
    await stopper
    assert events == ["cleanup done", "stopped raised"]


async def test_a_result_in_the_same_tick_as_stop_wins() -> None:
    token = CancellationToken()

    async def fn() -> str:
        await asyncio.sleep(0)
        token.cancel()
        return "ran"

    assert await SandboxManager(stop_token=token).unless_stopped(fn, tool="run_code") == "ran"


async def test_the_calls_own_error_propagates_unchanged() -> None:
    async def fn() -> str:
        raise SandboxLimitExceeded("full")

    with pytest.raises(SandboxLimitExceeded, match="full"):
        await SandboxManager(stop_token=CancellationToken()).unless_stopped(fn, tool="run_code")


async def test_cancelling_the_caller_cancels_the_call_once_and_awaits_its_cleanup() -> None:
    cancels = 0
    cleaned = asyncio.Event()
    started = asyncio.Event()

    async def fn() -> str:
        nonlocal cancels
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancels += 1
            await asyncio.sleep(0.05)
            cleaned.set()
            raise
        return "ran"

    task = asyncio.ensure_future(
        SandboxManager(stop_token=CancellationToken()).unless_stopped(fn, tool="run_code")
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
    assert cancels == 1


async def test_a_second_cancel_during_the_drain_still_waits_for_the_call(monkeypatch) -> None:
    monkeypatch.setattr(manager_mod, "_STOP_DRAIN_S", 0.3)
    token = CancellationToken()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def fn() -> str:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.sleep(0.15))
            finished.set()
            raise
        return "ran"

    task = asyncio.ensure_future(SandboxManager(stop_token=token).unless_stopped(fn, tool="run_code"))
    await started.wait()
    token.cancel()
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


async def test_drain_bound_logs_and_still_reports_stopped(monkeypatch, caplog) -> None:
    monkeypatch.setattr(manager_mod, "_STOP_DRAIN_S", 0.1)
    token = CancellationToken()
    started = asyncio.Event()
    release = asyncio.Event()

    async def fn() -> str:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            await asyncio.shield(release.wait())  # cleanup that hangs
            raise
        return "ran"

    task = asyncio.ensure_future(SandboxManager(stop_token=token).unless_stopped(fn, tool="run_code"))
    await started.wait()
    with caplog.at_level(logging.WARNING):
        token.cancel()
        with pytest.raises(SandboxCallStopped):
            await task
    assert "did not finish within 0.1s of Stop" in caplog.text
    release.set()
    await asyncio.sleep(0.01)
