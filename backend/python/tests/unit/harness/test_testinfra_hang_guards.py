"""TESTINFRA: a stuck test, or a stuck Node journal server under it, fails fast and says why instead of hanging.

In the SYNCMAIN gate an xdist worker running the engine-memory tests (``journal-http-server.ts`` under
``_NodeServer``) hung ~48 min although every test has a pytest-timeout. Two causes, each pinned here:

* pytest-timeout's signal method raises its failure once, from SIGALRM, into whatever the main thread runs. In an
  async test that is often a background task's step, and an asyncio task keeps an exception raised in its step as its
  own result: the test awaits on and nothing ends it. ``tests/support/timeout_backstop.py`` now ends the process a
  grace period later, naming the test (an xdist worker crash, replaced).
* ``_NodeServer`` killed ``npx`` alone on a failed start or stop, leaving the ts-node server it runs alive, and left
  the server's stdout undrained. It now runs as a process group that a failed start or a stop past its bound kills
  whole, and its stdout is drained.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.unit.harness import test_node_journal_contract as contract

BACKSTOP = Path(__file__).resolve().parents[2] / "support" / "timeout_backstop.py"

# A test whose own failure a busy background task swallows: the shape of an engine run's poll loops. The task never
# yields, so the alarm always lands in its step; one that yielded let the alarm land in the event loop now and then,
# which ended the test at its timeout and left nothing for the backstop to catch.
SWALLOWING_TEST = '''
import asyncio

import pytest


@pytest.mark.timeout(1)
async def test_waits_while_a_task_spins():
    async def spin():
        while True:
            sum(range(20_000))

    spinner = asyncio.create_task(spin())
    await asyncio.Event().wait()
    spinner.cancel()
'''

CONFTEST = f'''
import importlib.util

spec = importlib.util.spec_from_file_location("timeout_backstop", {str(BACKSTOP)!r})
backstop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backstop)


def pytest_configure(config):
    config.pluginmanager.register(backstop.TimeoutBackstop(), "pxh-timeout-backstop")
'''

# A process that stands in for npx: a child of its own (ts-node's server), its pid on stderr, then whatever it is told.
FAKE_NPX = '''
import os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(600)"])
print(child.pid, file=sys.stderr, flush=True)
mode = sys.argv[1]
if mode != "never-ready":
    print("READY 1 credential", flush=True)
if mode == "chatty":
    sys.stdout.write("x" * 4_000_000)
    sys.stdout.flush()
    open(sys.argv[2], "w").close()
    sys.stdin.read()
    sys.exit(0)
time.sleep(600)
'''


# Only the plugins the inner run needs. Autoloading every installed one (opik's imports nltk) cost ~3 s of CPU per
# process, three processes under -n 1, and pushed the run past its bound on a loaded gate.
INNER_PLUGINS = ("-p", "pytest_asyncio.plugin", "-p", "pytest_timeout", "-p", "xdist.plugin")


def _inner_pytest(
    tmp_path: Path, *, backstop: bool, timeout_s: float, workers: tuple[str, ...] = ("-p", "no:xdist")
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pytest.ini").write_text("[pytest]\nasyncio_mode = auto\n")
    (tmp_path / "test_swallowed.py").write_text(SWALLOWING_TEST)
    if backstop:
        (tmp_path / "conftest.py").write_text(CONFTEST)
    env = {**os.environ, "PXH_TIMEOUT_GRACE_S": "2", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *INNER_PLUGINS, *workers, "test_swallowed.py"]
    with subprocess.Popen(
        args, cwd=tmp_path, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    ) as proc:
        try:
            out, err = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            # The whole group: a hung run's xdist worker spins on, a CPU each, after its controller is killed.
            os.killpg(proc.pid, signal.SIGKILL)
            _ = proc.communicate()
            raise
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


def test_testinfra_a_swallowed_signal_timeout_hangs_without_the_backstop(tmp_path: Path) -> None:
    """The root cause: the alarm fires once at 1 s, a background task keeps its failure, the test never ends."""
    with pytest.raises(subprocess.TimeoutExpired):
        _ = _inner_pytest(tmp_path, backstop=False, timeout_s=8)


@pytest.mark.parametrize("workers", [("-p", "no:xdist"), ("-n", "1")], ids=["in-process", "xdist-worker"])
def test_testinfra_the_backstop_ends_a_swallowed_timeout_and_names_the_test(
    tmp_path: Path, workers: tuple[str, ...]
) -> None:
    done = _inner_pytest(tmp_path, backstop=True, timeout_s=25, workers=workers)
    assert done.returncode != 0
    output = done.stdout + done.stderr
    assert "test_swallowed.py::test_waits_while_a_task_spins is still running 2 s after its 1 s timeout fired" in output
    assert "most recent call first" in output
    if workers[0] == "-n":
        # xdist names the test the worker died in, fails it and carries on with a new worker.
        assert "crashed while running 'test_swallowed.py::test_waits_while_a_task_spins'" in output


def _gone(pid: int, within_s: float = 5) -> bool:
    deadline = time.monotonic() + within_s
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return True
        if state == "Z":
            return True
        time.sleep(0.05)
    return False


def test_testinfra_a_server_that_never_starts_fails_fast_and_leaves_nothing_running() -> None:
    # Nothing read (``[]``) is the 1 s ready wait running out; without that bound the start waits on the 600 s sleep.
    with pytest.raises(pytest.fail.Exception, match=r"did not start in 1 s: \[\]; stderr: (\d+)") as failed:
        _ = contract._NodeServer(command=[sys.executable, "-c", FAKE_NPX, "never-ready"], ready_timeout_s=1)  # pyright: ignore[reportPrivateUsage]
    child = int(str(failed.value).rsplit("stderr: ", 1)[1].split()[0])
    assert _gone(child), "the server's own child outlived the failed start"


def test_testinfra_a_server_that_ignores_stop_is_killed_whole_within_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(contract, "STOP_TIMEOUT_S", 1)
    server = contract._NodeServer(command=[sys.executable, "-c", FAKE_NPX, "deaf"], ready_timeout_s=10)  # pyright: ignore[reportPrivateUsage]
    server.control.close()
    child = int(server._stderr_tail().split()[0])  # pyright: ignore[reportPrivateUsage]
    real_wait = server.process.wait
    waits: list[float | None] = []

    def wait(timeout: float | None = None) -> int:
        waits.append(timeout)
        return real_wait(timeout)

    monkeypatch.setattr(server.process, "wait", wait)
    server.stop()
    assert waits and all(w == 1 for w in waits), f"stop waited past its bound: {waits}"
    assert server.process.returncode == -signal.SIGKILL, "the deaf server was killed, not waited out"
    assert _gone(child), "the server's own child outlived stop()"


def test_testinfra_what_the_server_writes_after_ready_never_waits_on_the_suite(tmp_path: Path) -> None:
    wrote = tmp_path / "wrote"
    server = contract._NodeServer(command=[sys.executable, "-c", FAKE_NPX, "chatty", str(wrote)], ready_timeout_s=10)  # pyright: ignore[reportPrivateUsage]
    try:
        deadline = time.monotonic() + 10
        while not wrote.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert wrote.exists(), "the server blocked writing its stdout"
    finally:
        server.control.close()
        server.stop()
