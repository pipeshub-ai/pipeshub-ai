"""SBXLEAK: what a test process registered to remove at exit is removed on every way out, and only a broker's objects
get the run's labels (``tests/support/sandbox_reaper.py``, ``docker_session.at_exit``, ``timeout_backstop.py``).

The Docker lanes' sandboxes and per-sandbox networks outlived their tests because every way a gate's test process
ends skipped ``atexit``: xdist's ``os._exit`` in ``tests/conftest.py``, pytest-timeout's ``thread`` method and the
hang guard (``os._exit``), and a worker process orphaned by its parent's death went on acquiring sandboxes. The
Docker cases are ``sandbox_broker/test_sbxleak_docker.py``; these pin the mechanisms without a daemon.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from tests.support import docker_session, sandbox_reaper
from tests.unit.harness.test_testinfra_hang_guards import (
    BACKSTOP,
    CONFTEST,
    INNER_PLUGINS,
)

PYTHON_ROOT = Path(__file__).resolve().parents[3]
MANAGED = {"io.pipeshub.g4.abcd1234.managed": "1", "io.pipeshub.g4.abcd1234.role": "sandbox", "runId": "r"}

# A test that registers an exit handler, as a lane's labels do, then hangs past its timeout.
HANGING_TEST = """
import os, time
from pathlib import Path

import pytest

from tests.support import docker_session


@pytest.mark.timeout(1, method={method!r})
def test_hangs():
    docker_session.at_exit(Path(os.environ["SBXLEAK_MARKER"]).write_text, "ran")
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            time.sleep(60)
        except BaseException:
            continue
"""


def _hang(tmp_path: Path, method: str, workers: tuple[str, ...]) -> tuple[subprocess.CompletedProcess[str], Path]:
    (tmp_path / "pytest.ini").write_text("[pytest]\nasyncio_mode = auto\n")
    (tmp_path / "test_hang.py").write_text(HANGING_TEST.format(method=method))
    (tmp_path / "conftest.py").write_text(CONFTEST)
    marker = tmp_path / "marker"
    env = {
        **os.environ,
        "PXH_TIMEOUT_GRACE_S": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(PYTHON_ROOT),
        "SBXLEAK_MARKER": str(marker),
    }
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *INNER_PLUGINS, *workers, "test_hang.py"]
    done = subprocess.run(args, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60, check=False)
    return done, marker


@pytest.mark.parametrize("workers", [("-p", "no:xdist"), ("-n", "1")], ids=["in-process", "xdist-worker"])
@pytest.mark.parametrize("method", ["thread", "signal"], ids=["thread-method-timeout", "hang-guard"])
def test_sbxleak7_a_timed_out_test_process_runs_its_exit_handlers_before_os_exit(
    tmp_path: Path, method: str, workers: tuple[str, ...]
) -> None:
    assert BACKSTOP.exists()
    done, marker = _hang(tmp_path, method, workers)
    assert done.returncode != 0
    assert marker.read_text() == "ran", done.stdout[-2000:] + done.stderr[-2000:]


def test_sbxleak8_exit_handlers_run_once_latest_first_and_never_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker_session, "_exit_handlers", [])
    monkeypatch.setattr(docker_session, "_atexit_registered", [True])
    ran: list[str] = []

    def boom() -> None:
        raise RuntimeError("a daemon that is gone")

    docker_session.at_exit(ran.append, "first")
    docker_session.at_exit(boom)
    docker_session.at_exit(ran.append, "last")
    docker_session.run_exit_handlers()
    docker_session.run_exit_handlers()
    assert ran == ["last", "first"]


def test_sbxleak9_only_a_brokers_objects_get_the_runs_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(sandbox_reaper.RUN_ENV, "host:1:run")
    monkeypatch.setenv(sandbox_reaper.OWNER_ENV, "host:2:owner")
    seen: list[Any] = []

    def create(_self: object, *_args: object, **kwargs: Any) -> None:  # noqa: ANN401
        seen.append(kwargs.get("labels"))

    wrapped = sandbox_reaper._labelling(create)  # pyright: ignore[reportPrivateUsage]
    wrapped(None, "pxh-x", labels=dict(MANAGED))
    wrapped(None, "pxh-rig", labels={"pipeshub.harness.owner": "x-lead"})
    wrapped(None, "pxh-plain")
    wrapped(None, "pxh-unmanaged", labels={"io.pipeshub.g4.abcd1234.managed": "0"})
    assert seen[0] == {
        **MANAGED,
        sandbox_reaper.RUN: "host:1:run",
        sandbox_reaper.OWNER: "host:2:owner",
        docker_session.USER: docker_session._user(),  # pyright: ignore[reportPrivateUsage]
    }
    assert seen[1:] == [{"pipeshub.harness.owner": "x-lead"}, None, {"io.pipeshub.g4.abcd1234.managed": "0"}]


def test_sbxleak9_a_suites_resources_carry_the_run_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(docker_session.RUN_ENV, "host:1:run")
    monkeypatch.setenv(docker_session.OWNER_ENV, "host:2:owner")
    monkeypatch.setattr(docker_session, "_registered", {"sbxleak-suite"})
    labels = docker_session.labels("sbxleak-suite")
    assert (labels[docker_session.RUN], labels[docker_session.OWNER]) == ("host:1:run", "host:2:owner")


class _Api:
    """Lists what carries a label, as Docker's filters do, and records what is removed."""

    def __init__(self, items: dict[str, dict[str, dict[str, str]]]) -> None:
        self.items = items
        self.removed: list[str] = []

    def _match(self, kind: str, filters: dict[str, list[str]]) -> list[tuple[str, dict[str, str]]]:
        wanted = [f.split("=", 1) for f in filters["label"]]
        return [
            (n, labels)
            for n, labels in self.items[kind].items()
            if all((k in labels) if len(kv) == 1 else labels.get(k) == kv[1] for kv in wanted for k in kv[:1])
        ]

    def containers(self, *, all: bool, filters: dict[str, list[str]]) -> list[dict[str, Any]]:  # noqa: A002
        assert all
        return [{"Id": n, "Names": [f"/{n}"], "Labels": labels} for n, labels in self._match("container", filters)]

    def remove_container(self, container: str, **_: Any) -> None:  # noqa: ANN401
        self.removed.append(container)

    def networks(self, *, filters: dict[str, list[str]]) -> list[dict[str, Any]]:
        return [{"Id": n, "Name": n, "Labels": labels} for n, labels in self._match("network", filters)]

    def inspect_network(self, network: str) -> dict[str, Any]:
        return {"Containers": {}}

    def remove_network(self, network: str) -> None:
        self.removed.append(network)

    def volumes(self, *, filters: dict[str, list[str]]) -> dict[str, Any]:
        return {"Volumes": [{"Name": n, "Labels": labels} for n, labels in self._match("volume", filters)]}

    def remove_volume(self, name: str, force: bool) -> None:
        self.removed.append(name)


class _Client:
    def __init__(self, api: _Api) -> None:
        self.api = api


def test_sbxleak10_the_reaper_removes_only_what_carries_this_processs_or_this_runs_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = docker_session._user()  # pyright: ignore[reportPrivateUsage]
    run = f"{socket.gethostname()}:{os.getpid()}:run"
    monkeypatch.setenv(sandbox_reaper.RUN_ENV, run)
    monkeypatch.setenv(sandbox_reaper.OWNER_ENV, "host:7:me")
    mine = {sandbox_reaper.OWNER: "host:7:me", sandbox_reaper.RUN: run, docker_session.USER: user}
    workers = {sandbox_reaper.OWNER: "host:8:worker", sandbox_reaper.RUN: run, docker_session.USER: user}
    api = _Api({
        "container": {
            "pxh-g4-a-sbx": mine,
            "pxh-g4-b-sbx": workers,
            "pxh-g4-other-run": {**workers, sandbox_reaper.RUN: "host:9:other"},
            "pxh-g4-other-user": {**mine, docker_session.USER: "someone-else"},
            "unlabelled": {},
        },
        "network": {"pxh-g4-a-net": mine, "pxh-g4-orphan-net": {}},
        "volume": {"pxh-g4-a-0": mine},
    })
    client = _Client(api)
    assert sorted(sandbox_reaper.reap_owned(client)) == ["pxh-g4-a-0", "pxh-g4-a-net", "pxh-g4-a-sbx"]
    api.removed.clear()
    assert sandbox_reaper.reap_run(client) == ["pxh-g4-a-sbx", "pxh-g4-b-sbx", "pxh-g4-a-net", "pxh-g4-a-0"]
    monkeypatch.setenv(sandbox_reaper.RUN_ENV, "host:1:a-run-this-process-did-not-start")
    assert not sandbox_reaper.owns_run()
    assert sandbox_reaper.reap_run(client) == []


def _dead_owner() -> str:
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    _ = exited.wait()
    return f"{socket.gethostname()}:{exited.pid}:dead"


def test_sbxleak12_the_startup_sweep_removes_only_a_dead_owners_run_objects(monkeypatch: pytest.MonkeyPatch) -> None:
    """A run SIGKILLed before any exit handler ran: the next session removes what its owner left, and nothing of a
    live owner, of another host's owner, of another user, or without the run's labels."""
    user = docker_session._user()  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setenv(sandbox_reaper.OWNER_ENV, f"{socket.gethostname()}:{os.getpid()}:me")
    dead = _dead_owner()
    killed = {sandbox_reaper.RUN: "h:1:killed-run", sandbox_reaper.OWNER: dead, docker_session.USER: user}
    live = {**killed, sandbox_reaper.OWNER: f"{socket.gethostname()}:{os.getppid()}:live"}
    api = _Api({
        "container": {
            "pxh-g4-killed-sbx": killed,
            "pxh-g4-live-sbx": live,
            "pxh-g4-elsewhere-sbx": {**killed, sandbox_reaper.OWNER: "another-host:1:x"},
            "pxh-g4-other-user-sbx": {**killed, docker_session.USER: "someone-else"},
            "pxh-g4-unlabelled": {sandbox_reaper.OWNER: dead},
        },
        "network": {"pxh-g4-killed-net": killed, "pxh-g4-live-net": live},
        "volume": {"pxh-g4-killed-0": killed},
    })
    assert sandbox_reaper.dead_owners(_Client(api)) == {dead}
    assert sandbox_reaper.reap_dead_runs(_Client(api)) == ["pxh-g4-killed-sbx", "pxh-g4-killed-net", "pxh-g4-killed-0"]


# A test process that spawns a worker as the lanes do, then dies without any teardown.
ORPHANING_PARENT = """
import os, subprocess, sys
from tests.support.sandbox_reaper import die_with_parent

child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], preexec_fn=die_with_parent)
print(child.pid, flush=True)
os._exit(0)
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="PR_SET_PDEATHSIG is Linux's")
def test_sbxleak11_a_lane_worker_dies_with_the_test_process_that_spawned_it() -> None:
    done = subprocess.run(
        [sys.executable, "-c", ORPHANING_PARENT], cwd=PYTHON_ROOT, env={**os.environ, "PYTHONPATH": str(PYTHON_ROOT)},
        capture_output=True, text=True, timeout=30, check=True,
    )
    child = int(done.stdout.strip())
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            state = Path(f"/proc/{child}/stat").read_text().split(")", 1)[1].split()[0]
        # Reaped between the open and the read, /proc answers ESRCH instead of ENOENT: gone all the same.
        except (FileNotFoundError, ProcessLookupError):
            return
        if state == "Z":
            return
        time.sleep(0.05)
    os.kill(child, 9)
    pytest.fail("the worker outlived the test process that spawned it")
