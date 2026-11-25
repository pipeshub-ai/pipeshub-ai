"""SBXLEAK: a test that fails, times out or is killed mid-run leaves no sandbox, network or volume behind.

Each case runs one test of ``sbxleak_inner.py`` in a pytest process of its own, as a gate runs it: the test acquires a
sandbox with egress (a container, its own network, its volumes) from a broker no fixture tears down, next to the
module's egress stack, then fails, hangs past pytest-timeout's ``thread`` method, hangs where the ``signal`` method's
failure is swallowed (the hang guard's ``os._exit``), or is sent SIGTERM; alone and as an xdist worker. Afterwards
nothing the broker made (its namespace's ``managed`` label) and nothing of the egress stack (its suffix) is left: the
run's reaper (``tests/support/sandbox_reaper.py``) and the process's exit handlers (``docker_session.at_exit``)
removed them on every way out.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tests.support import docker_session, sandbox_reaper
from tests.unit.agent_loop_lib.sandbox.contract.conftest import skip_without_docker

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

PYTHON_ROOT = Path(__file__).resolve().parents[4]
INNER = "tests/unit/harness/sandbox_broker/sbxleak_inner.py"
# What a pytest process inherits from this one and must not: it is a run of its own, not this run's worker.
_NOT_INHERITED = (
    sandbox_reaper.RUN_ENV,
    sandbox_reaper.OWNER_ENV,
    "PYTEST_XDIST_WORKER",
    "PYTEST_XDIST_WORKER_COUNT",
    "PYTEST_XDIST_TESTRUNUID",
    "PYTEST_CURRENT_TEST",
)


class Inner:
    def __init__(self, tmp_path: Path) -> None:
        self.namespace = f"io.pipeshub.sbxleak.{uuid.uuid4().hex[:8]}"
        self.ready = tmp_path / "ready.json"
        self.log = tmp_path / "inner.log"

    def start(self, test: str, *args: str) -> subprocess.Popen[bytes]:
        env = {k: v for k, v in os.environ.items() if k not in _NOT_INHERITED}
        env |= {"SBXLEAK_NAMESPACE": self.namespace, "SBXLEAK_READY": str(self.ready), "PXH_TIMEOUT_GRACE_S": "2"}
        with self.log.open("wb") as out:
            return subprocess.Popen(
                [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", f"{INNER}::{test}", *args],
                cwd=PYTHON_ROOT, env=env, stdout=out, stderr=subprocess.STDOUT,
            )

    def made(self) -> dict[str, Any]:
        assert self.ready.exists(), f"the inner test never acquired its sandbox:\n{self.output()}"
        return json.loads(self.ready.read_text())

    def output(self) -> str:
        return self.log.read_text(errors="replace")[-4000:] if self.log.exists() else ""

    def wait_ready(self, process: subprocess.Popen[bytes], timeout_s: float = 300) -> None:
        deadline = time.monotonic() + timeout_s
        while not self.ready.exists():
            assert process.poll() is None, f"the inner run ended before it acquired:\n{self.output()}"
            assert time.monotonic() < deadline, f"no sandbox after {timeout_s:g} s:\n{self.output()}"
            time.sleep(0.5)


def left(client: Any, inner: Inner, stack: str | None) -> dict[str, list[str]]:  # noqa: ANN401 - docker.DockerClient
    """Whatever of the inner run is still on the daemon, by kind: the broker's objects, and the egress stack's by
    its suffix."""
    api = client.api
    managed = {"label": [f"{inner.namespace}.managed=1"]}
    found = {
        "containers": [str(c["Names"][0]).lstrip("/") for c in api.containers(all=True, filters=managed)],
        "networks": [str(n["Name"]) for n in api.networks(filters=managed) or []],
        "volumes": [str(v["Name"]) for v in (api.volumes(filters=managed) or {}).get("Volumes") or []],
    }
    if stack:
        named = {"name": [f"pxh-p54-.*-{stack}$"]}
        found["containers"] += [str(c["Names"][0]).lstrip("/") for c in api.containers(all=True, filters=named)]
        found["networks"] += [str(n["Name"]) for n in api.networks(filters=named) or []]
    return found


NOTHING: dict[str, list[str]] = {"containers": [], "networks": [], "volumes": []}


@pytest.fixture
def client() -> Iterator[Any]:
    skip_without_docker()
    import docker

    docker_client = docker.from_env()
    try:
        docker_client.images.get("python:3.12-slim")
    except Exception:
        docker_client.close()
        pytest.skip("the egress stack's helper image is not present")
    yield docker_client
    docker_client.close()


@pytest.fixture
def inner(tmp_path: Path, client: Any) -> Iterator[Inner]:  # noqa: ANN401
    """The inner run's own objects, removed here only if the case failed to see them go."""
    run = Inner(tmp_path)
    yield run
    stack = json.loads(run.ready.read_text())["stack"] if run.ready.exists() else None
    found = left(client, run, stack)
    for name in found["containers"]:
        with contextlib.suppress(Exception):
            _ = docker_session.remove_container(client.api, name, v=True)
    for name in found["networks"]:
        with contextlib.suppress(Exception):
            client.api.remove_network(name)
    for name in found["volumes"]:
        with contextlib.suppress(Exception):
            client.api.remove_volume(name, force=True)


CASES = [
    pytest.param("test_acquires_then_fails", (), id="sbxleak1-a-failed-test"),
    pytest.param("test_acquires_then_hangs_past_a_thread_timeout", (), id="sbxleak2-a-thread-method-timeout"),
    pytest.param(
        "test_acquires_then_hangs_where_the_signal_timeout_cannot_end_it", (), id="sbxleak3-the-hang-guards-exit"
    ),
    pytest.param("test_acquires_then_hangs_past_a_thread_timeout", ("-n", "1"), id="sbxleak4-a-crashed-xdist-worker"),
]


@pytest.mark.parametrize(("test", "args"), CASES)
def test_sbxleak_a_test_that_dies_mid_run_leaves_no_sandbox_network_or_volume(
    client: Any, inner: Inner, test: str, args: tuple[str, ...]  # noqa: ANN401
) -> None:
    process = inner.start(test, *args)
    code = process.wait(timeout=420)
    made = inner.made()
    assert code != 0, inner.output()
    assert left(client, inner, made["stack"]) == NOTHING, inner.output()


@pytest.mark.parametrize("args", [(), ("-n", "1")], ids=["sbxleak5-sigterm", "sbxleak6-sigterm-under-xdist"])
def test_sbxleak_a_run_ended_by_sigterm_leaves_nothing_and_its_objects_carry_the_runs_labels(
    client: Any, inner: Inner, args: tuple[str, ...]  # noqa: ANN401
) -> None:
    process = inner.start("test_acquires_then_waits_for_a_signal", *args)
    try:
        inner.wait_ready(process)
        made = inner.made()
        sandbox = client.api.containers(filters={"label": [f"{inner.namespace}.id={made['sandbox']}"]})
        assert len(sandbox) == 1, sandbox
        labels = sandbox[0]["Labels"]
        assert labels[sandbox_reaper.RUN].split(":")[1] == str(process.pid)
        assert labels[sandbox_reaper.OWNER].split(":")[1] == str(made["pid"])
        network = client.api.networks(filters={"label": [f"{inner.namespace}.id={made['sandbox']}"]})
        assert [n["Labels"][sandbox_reaper.RUN] for n in network] == [labels[sandbox_reaper.RUN]]
        process.send_signal(signal.SIGTERM)
        _ = process.wait(timeout=180)
    finally:
        if process.poll() is None:
            process.kill()
            _ = process.wait()
    assert left(client, inner, made["stack"]) == NOTHING, inner.output()


def test_sbxleak12_a_sigkilled_runs_objects_are_removed_by_the_next_session_and_a_live_runs_are_not(
    client: Any, inner: Inner  # noqa: ANN401
) -> None:
    """SIGKILL runs no exit handler: the next session's startup sweep (``reap_dead_runs``) removes what the killed
    run's owner left, its sandbox, network, volumes and egress stack, and nothing while that owner lived."""
    process = inner.start("test_acquires_then_waits_for_a_signal")
    try:
        inner.wait_ready(process)
        made = inner.made()
        before = left(client, inner, made["stack"])
        assert before["containers"] and before["networks"], before
        assert not set(sandbox_reaper.reap_dead_runs(client)) & {*before["containers"], *before["networks"]}
        assert left(client, inner, made["stack"]) == before
        process.kill()
        _ = process.wait(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
            _ = process.wait()
    removed = set(sandbox_reaper.reap_dead_runs(client))
    assert {*before["containers"], *before["networks"], *before["volumes"]} <= removed, removed
    assert left(client, inner, made["stack"]) == NOTHING, inner.output()
