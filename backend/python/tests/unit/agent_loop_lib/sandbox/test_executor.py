"""Tests for app.agent_loop_lib.sandbox.coding.executor.CodeExecutor.

Focused on the artifact-detection diff — a regression here silently turns
every code run's own entry script into a spurious "artifact" that gets
uploaded to blob storage and shown to the user as a download card.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.coding.base import CodeRequest
from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager
from app.agent_loop_lib.sandbox.coding.executor import CodeExecutor, ExecutionLimits

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def executor(tmp_path) -> CodeExecutor:
    working_dir = str(tmp_path)
    env = EnvironmentManager(working_dir)
    return CodeExecutor(working_dir, env)


class TestArtifactDetection:
    async def test_entry_file_itself_is_not_reported_as_an_artifact(self, executor: CodeExecutor) -> None:
        """The entry file (main.py) is written to disk on every run, between
        the before/after mtime snapshots, so a naive diff always flags it as
        "new" — it must be excluded, or every code execution would report
        its own script as a downloadable artifact."""
        result = await executor.execute(
            CodeRequest(code="print('hello')", language="python", timeout=15.0)
        )
        assert result.success
        assert "main.py" not in result.artifacts

    async def test_real_output_file_is_still_reported_as_an_artifact(self, executor: CodeExecutor) -> None:
        result = await executor.execute(
            CodeRequest(
                code="open('report.txt', 'w').write('hi')",
                language="python",
                timeout=15.0,
            )
        )
        assert result.success
        assert result.artifacts == ["report.txt"]

    async def test_custom_entry_file_is_also_excluded(self, executor: CodeExecutor) -> None:
        result = await executor.execute(
            CodeRequest(
                code="open('data.csv', 'w').write('a,b\\n1,2')",
                language="python",
                timeout=15.0,
                entry_file="custom_script.py",
            )
        )
        assert result.success
        assert "custom_script.py" not in result.artifacts
        assert "data.csv" in result.artifacts


class TestMemoryLimitDoesNotBreakJitRuntimes:
    """`RLIMIT_AS` caps VIRTUAL address space, which a JIT reserves far more
    of than it ever commits. V8 sizes that reservation from the limit it
    observes, so the outcome is non-monotonic — measured in the app image with
    tsx, 1.5 GB and 6 GB both start fine while 3 GB dies with
    "Fatal process out of memory: Failed to reserve virtual memory for
    CodeRange" (exit 133). There is no safe value to pick: it depends on the
    limit, the host's physical memory and the Node build.

    `RLIMIT_DATA` bounds the data segment and private anonymous mappings
    instead, which is what actually correlates with memory the sandbox uses.
    Verified in the same image: a hello-world runs under it at every limit
    tried, while a runaway 4 GB allocation is still killed (and Python gets
    `MemoryError` rather than completing).
    """

    def _applied_limits(self, limits: ExecutionLimits) -> dict:
        """The `(resource, value)` pairs the preexec fn would set."""
        import resource
        from unittest.mock import patch

        from app.agent_loop_lib.sandbox.coding.executor import _rlimit_preexec_fn

        captured: dict = {}
        with patch.object(
            resource, "setrlimit",
            side_effect=lambda res, val: captured.__setitem__(res, val),
        ):
            _rlimit_preexec_fn(limits)()
        return captured

    def test_address_space_is_not_capped(self) -> None:
        import resource

        applied = self._applied_limits(ExecutionLimits())
        assert resource.RLIMIT_AS not in applied, (
            "capping RLIMIT_AS makes V8 fail to reserve its CodeRange"
        )

    def test_memory_is_still_capped(self) -> None:
        """Dropping RLIMIT_AS must not mean dropping the cap entirely."""
        import resource

        applied = self._applied_limits(ExecutionLimits(max_memory_bytes=1234))
        assert applied.get(resource.RLIMIT_DATA) == (1234, 1234)

    def test_the_other_limits_are_unchanged(self) -> None:
        import resource
        from unittest.mock import patch

        with patch("app.agent_loop_lib.sandbox.coding.executor._BUDGET_IN_OWN_NAMESPACE", False):
            applied = self._applied_limits(
                ExecutionLimits(max_cpu_seconds=7, max_file_size_bytes=99, max_processes=5)
            )
        assert applied.get(resource.RLIMIT_CPU) == (7, 7)
        assert applied.get(resource.RLIMIT_FSIZE) == (99, 99)
        assert applied.get(resource.RLIMIT_NPROC) == (5, 5)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_NPROC counts threads and /proc tells them on Linux")
class TestProcessBudgetIsOnTopOfWhatTheUserRuns:
    """H5 PL: RLIMIT_NPROC counts every task of the real UID, so an absolute limit failed runs (EAGAIN on fork,
    Node aborting in uv_thread_create) whenever the account's other work passed it. The budget is now added to
    what the user runs at spawn time; these cases hold whatever else the host runs."""

    @pytest.mark.parametrize(
        ("own", "entered", "in_use", "hard", "expected"),
        [
            (7, False, 900, 10_000, 64),  # bubblewrap gave the run a namespace: the budget alone, none nested
            (1, True, 900, 10_000, 64),  # a namespace entered for the run: the budget alone
            (1, False, 900, 10_000, 964),  # none can be made: the account's tasks plus the budget
            (1, False, None, 10_000, 64),  # nor can the tasks be counted: the budget alone
            (1, False, 900, 500, 500),  # never above the hard limit
        ],
    )
    def test_pl1_the_limit_is_the_budget_in_the_runs_namespace_else_on_top_of_the_users_tasks(
        self, own: int, entered: bool, in_use: int | None, hard: int, expected: int
    ) -> None:
        from app.agent_loop_lib.sandbox.coding.process_budget import (
            choose_process_limit,
        )

        entries: list[bool] = []

        def enter() -> bool:
            entries.append(entered)
            return entered

        limit = choose_process_limit(
            64, 1, own_namespace=lambda: own, enter_namespace=enter, count_tasks=lambda: in_use, hard_limit=hard
        )
        assert limit == expected
        assert entries == ([] if own != 1 else [entered])

    def test_pl2_tasks_are_counted_per_uid_threads_included(self, tmp_path) -> None:
        from app.agent_loop_lib.sandbox.coding.process_budget import user_task_count

        uid = os.getuid()
        for pid, threads in (("11", 3), ("12", 1)):
            (tmp_path / pid).mkdir()
            (tmp_path / pid / "status").write_text(f"Name:\tx\nThreads:\t{threads}\n")
        (tmp_path / "self").mkdir()
        assert user_task_count(uid, str(tmp_path)) == 4
        assert user_task_count(uid + 1, str(tmp_path)) is None
        assert user_task_count(uid, str(tmp_path / "missing")) is None

    async def test_pl3_a_small_budget_still_runs_threads_however_many_the_user_runs(self, tmp_path) -> None:
        hold = threading.Event()
        # This process alone now runs more tasks than the budget: an absolute limit of 32 would fail the run.
        idle = [threading.Thread(target=hold.wait, daemon=True) for _ in range(40)]
        for thread in idle:
            thread.start()
        try:
            executor = CodeExecutor(str(tmp_path), EnvironmentManager(str(tmp_path)), limits=ExecutionLimits(max_processes=32))
            code = "import threading, subprocess, sys\n" \
                "t = threading.Thread(target=print, args=('thread ok',)); t.start(); t.join()\n" \
                "subprocess.run([sys.executable, '-c', 'print(\"child ok\")'], check=True)\n"
            result = await executor.execute(CodeRequest(code=code, language="python", timeout=60.0))
        finally:
            hold.set()
        assert result.success, result.stderr
        assert "thread ok" in result.stdout and "child ok" in result.stdout

    async def test_pl4_a_fork_bomb_still_stops_within_the_budget(self, tmp_path) -> None:
        executor = CodeExecutor(str(tmp_path), EnvironmentManager(str(tmp_path)), limits=ExecutionLimits(max_processes=32))
        code = "import os, time\n" \
            "made = 0\n" \
            "try:\n" \
            "    for _ in range(400):\n" \
            "        if os.fork() == 0:\n" \
            "            time.sleep(3); os._exit(0)\n" \
            "        made += 1\n" \
            "except OSError as e:\n" \
            "    print('refused after', made, e.errno)\n"
        result = await executor.execute(CodeRequest(code=code, language="python", timeout=60.0))
        assert "refused after" in result.stdout, result.stdout + result.stderr
        made = int(result.stdout.split("refused after", 1)[1].split()[0])
        assert made < 400


_THREAD_AND_CHILD = (
    "import threading, subprocess, sys\n"
    "t = threading.Thread(target=print, args=('thread ok',)); t.start(); t.join()\n"
    "subprocess.run([sys.executable, '-c', 'print(\"child ok\")'], check=True)\n"
)

_HOLD_THE_WHOLE_BUDGET = (
    "import os\n"
    "r, w = os.pipe()\n"
    "held = 0\n"
    "try:\n"
    "    for _ in range(1000):\n"
    "        if os.fork() == 0:\n"
    "            os.close(w); os.read(r, 1); os._exit(0)\n"
    "        held += 1\n"
    "except OSError:\n"
    "    pass\n"
    "print('held', held, flush=True)\n"
)


class _Gate:
    """A FIFO the sandboxed code opens before its work. Its limits are fixed by then, so whatever the test does
    between `opened` and `release` happens while the run is alive, in the order the case needs, with no timing."""

    def __init__(self, path: Path) -> None:
        os.mkfifo(path)
        self._path = path
        self._fd: int | None = None
        self.code = f"open({path.name!r}).read()\n"

    async def opened(self, run: asyncio.Task) -> None:
        opener = asyncio.ensure_future(asyncio.to_thread(os.open, self._path, os.O_WRONLY))
        await asyncio.wait({opener, run}, return_when=asyncio.FIRST_COMPLETED)
        if not opener.done():
            os.close(os.open(self._path, os.O_RDONLY | os.O_NONBLOCK))
            os.close(await opener)
            result = run.result()
            pytest.fail(f"the run ended before reaching its gate: {result.stdout}{result.stderr}")
        self._fd = opener.result()

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None


@contextmanager
def _threads_held(count: int) -> Iterator[None]:
    """Other work of the same user: ``count`` more tasks under this UID until the block ends."""
    hold = threading.Event()
    threads = [threading.Thread(target=hold.wait, daemon=True) for _ in range(count)]
    for thread in threads:
        thread.start()
    try:
        yield
    finally:
        hold.set()
        for thread in threads:
            thread.join()


def _gated_executor(directory: Path, budget: int) -> CodeExecutor:
    directory.mkdir(mode=0o700)  # a sandbox folder is private (R13-ROOT); a group-writable one is refused
    return CodeExecutor(str(directory), EnvironmentManager(str(directory)), limits=ExecutionLimits(max_processes=budget))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="per-run process budgets need Linux user namespaces")
class TestProcessBudgetIsTheRunsOwn:
    """H10: RLIMIT_NPROC counts every task of the UID, so a budget added to a count taken at spawn is spent by any
    task the user starts afterwards: other test workers, other agents, other sandbox runs."""

    async def test_pl5_work_the_user_starts_after_the_run_began_does_not_spend_its_budget(self, tmp_path) -> None:
        executor = _gated_executor(tmp_path / "run", 32)
        gate = _Gate(tmp_path / "run" / "gate")
        run = asyncio.ensure_future(
            executor.execute(CodeRequest(code=gate.code + _THREAD_AND_CHILD, language="python", timeout=60.0))
        )
        await gate.opened(run)
        with _threads_held(256):
            gate.release()
            result = await run
        assert result.success, result.stderr
        assert "thread ok" in result.stdout and "child ok" in result.stdout

    async def test_pl6_a_run_holding_its_whole_budget_leaves_another_runs_budget_whole(self, tmp_path) -> None:
        first = _gated_executor(tmp_path / "a", 32)
        second = _gated_executor(tmp_path / "b", 256)
        first_gate, second_gate = _Gate(tmp_path / "a" / "gate"), _Gate(tmp_path / "b" / "gate")
        first_run = asyncio.ensure_future(
            first.execute(CodeRequest(code=first_gate.code + _THREAD_AND_CHILD, language="python", timeout=60.0))
        )
        await first_gate.opened(first_run)
        second_run = asyncio.ensure_future(
            second.execute(CodeRequest(code=_HOLD_THE_WHOLE_BUDGET + second_gate.code + "os.close(w)\n",
                                       language="python", timeout=60.0))
        )
        try:
            await second_gate.opened(second_run)
            first_gate.release()
            first_result = await first_run
        finally:
            first_gate.release()
            second_gate.release()
        second_result = await second_run
        assert first_result.success, first_result.stderr
        assert "thread ok" in first_result.stdout and "child ok" in first_result.stdout
        held = int(second_result.stdout.split("held", 1)[1].split()[0])
        assert 0 < held < 256

    async def test_pl7_the_run_keeps_its_own_user_id(self, executor: CodeExecutor) -> None:
        result = await executor.execute(
            CodeRequest(code="import os; print(os.getuid())", language="python", timeout=15.0)
        )
        assert result.success, result.stderr
        assert result.stdout.strip() == str(os.getuid())

    def test_pl8_inside_a_namespace_confinement_made_the_budget_applies_there_with_none_nested(self) -> None:
        """bubblewrap gives each run a user namespace before the budget step runs inside it; a stand-in
        confinement layer here enters one the same way and then starts the budget step."""
        from app.agent_loop_lib.sandbox.coding import process_budget

        report = "readlink /proc/self/ns/user; grep 'Max processes' /proc/self/limits"
        confinement = (
            "import os, sys\n"
            f"sys.path.insert(0, {os.path.dirname(process_budget.__file__)!r})\n"
            "from process_budget import enter_own_user_namespace\n"
            "assert enter_own_user_namespace()\n"
            "print(os.readlink('/proc/self/ns/user'), flush=True)\n"
            "os.execvp(sys.argv[1], sys.argv[1:])\n"
        )
        confined = [sys.executable, "-I", "-S", "-c", confinement,
                    *process_budget.budget_command(["sh", "-c", report], 32)]
        lines = subprocess.run(confined, capture_output=True, text=True, check=True).stdout.splitlines()
        assert lines[0] == lines[1], "the budget nested a second namespace inside the one confinement made"
        assert lines[2].split()[2:4] == ["32", "32"]
