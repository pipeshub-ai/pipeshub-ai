"""A run's process group is killed even after its first process has exited.

A script can start a child that keeps the run's output pipes open and then
exit. The run then waits on the pipes until its timeout (or a Stop), and the
kill must still reach the child: the group outlives its first process.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time

import pytest

from app.agent_loop_lib.sandbox.coding.environment import _kill_process_tree

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX-only")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers signal 0; it is gone for this purpose.
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


async def _eventually(predicate, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while True:
        if predicate() or time.monotonic() >= deadline:
            return predicate()
        await asyncio.sleep(0.05)


async def _start_leader_with_background_child() -> tuple[asyncio.subprocess.Process, int]:
    """A group leader that starts a `sleep` on the same pipes and exits at once."""
    code = (
        "import subprocess, sys\n"
        "p = subprocess.Popen(['sleep', '120'])\n"
        "sys.stderr.write(str(p.pid) + '\\n'); sys.stderr.flush()\n"
    )
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", code,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    assert proc.stderr is not None
    line = await asyncio.wait_for(proc.stderr.readline(), timeout=30)
    child = int(line.strip())
    # Let the leader exit and be reaped, as it is while the run waits on the
    # pipes. (`proc.wait()` itself would wait for the pipes to close too.)
    assert await _eventually(lambda: proc.returncode is not None, 30)
    return proc, child


async def test_the_background_child_is_killed_after_its_leader_exited() -> None:
    proc, child = await _start_leader_with_background_child()
    try:
        assert _alive(child)

        await _kill_process_tree(proc)

        assert await _eventually(lambda: not _alive(child), 5), "the child kept running after the group kill"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(child, 9)


async def test_a_group_that_is_already_gone_is_not_an_error() -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "pass", start_new_session=True,
    )
    await proc.wait()

    await _kill_process_tree(proc)
