"""R46-LANE: the e2e lane's own scripts.

- R44STOP-FU-1: a stop of the lane's app container reaches the processes themselves, the durable worker and the
  query service first (they drain), the rest after them, and the container exits 0 (``app_from_source.sh``). The
  old trap signalled the wrapper shells only, so the worker died with the container and never drained.
- ST-4: a stack's store list names every one-shot job a store waits for (etcd waits for etcd-tls's certificates),
  as a job run to exit 0 before the stores start (``run.sh stack_stores``/``up_stack``).
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from typing import TYPE_CHECKING

import pytest

from tests.cohesion.compose_files import BASE as BASE_COMPOSE
from tests.cohesion.compose_files import compose_services
from tests.cohesion.support import REPO_ROOT

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

E2E = REPO_ROOT / "deployment" / "e2e"
RUN_SH = E2E / "run.sh"
# What `run.sh matrix-up` (the lane the ST-n profiles run on) hands Compose, in its order.
MATRIX_FILES = (BASE_COMPOSE, E2E / "docker-compose.e2e.yml", E2E / "docker-compose.matrix.yml")
APP_FROM_SOURCE = E2E / "app_from_source.sh"

# A stand-in for `python /e2e/pyrun.py <module>`: notes its start, and on TERM takes a moment (a drain) and exits 0.
FAKE_PYTHON = """#!/usr/bin/env bash
mod="${@: -1}"
echo "start $mod $$" >>"$EVENTS"
trap 'echo "term $mod" >>"$EVENTS"; sleep 0.5; echo "done $mod" >>"$EVENTS"; exit 0' TERM
while :; do sleep 0.1; done
"""


def _function(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{") + 1
    return text[start : text.index("\n}\n", start) + 3]


def _events(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _wait_for(predicate: Callable[[], bool], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return predicate()


def test_r46lane_fu1_a_stop_drains_the_worker_and_query_first_then_stops_the_rest_and_exits_0(tmp_path: Path) -> None:
    bin_dir, logs, events = tmp_path / "bin", tmp_path / "logs", tmp_path / "events"
    bin_dir.mkdir()
    logs.mkdir()
    fake = bin_dir / "python"
    fake.write_text(FAKE_PYTHON, encoding="utf-8")
    fake.chmod(0o755)
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "E2E_LOGS_DIR": str(logs),
        "E2E_DURABLE": "1",
        # The durable lane runs with the harness switched on (ADR-OPS-06); run.sh off runs neither worker nor gateway.
        "AGENT_HARNESS_WORKER_ENABLED": "true",
        "E2E_DRAIN_WAIT_S": "10",
        # rotate_logs.sh's sleep outlives the script's stop by at most this.
        "E2E_LOG_CHECK_SECONDS": "1",
        "EVENTS": str(events),
    }
    modules = {"app.harness_main", "app.query_main", "app.connectors_main", "app.model_gateway_main"}
    out_file = tmp_path / "out"
    with out_file.open("wb") as out:
        proc = subprocess.Popen(["bash", str(APP_FROM_SOURCE)], env=env, stdout=out, stderr=subprocess.STDOUT)
    try:
        started = lambda: {line.split()[1] for line in _events(events) if line.startswith("start ")}  # noqa: E731
        assert _wait_for(lambda: modules <= started(), 15), f"started: {sorted(started())}"
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
        assert proc.returncode == 0, out_file.read_text(errors="replace")[-2000:]
    finally:
        if proc.poll() is None:
            proc.kill()
        for line in _events(events):
            if line.startswith("start "):
                with contextlib.suppress(ProcessLookupError, ValueError):
                    os.kill(int(line.split()[2]), signal.SIGKILL)

    seen = _events(events)
    for mod in modules:
        assert f"term {mod}" in seen and f"done {mod}" in seen, f"{mod} was not stopped itself: {seen}"
    for drained in ("app.harness_main", "app.query_main"):
        for rest in ("app.connectors_main", "app.model_gateway_main"):
            assert seen.index(f"done {drained}") < seen.index(f"term {rest}"), f"{drained} drains before {rest}: {seen}"
    assert sum(line.startswith("start app.harness_main") for line in seen) == 1, f"the worker restarted: {seen}"
    assert (logs / "worker.pid").exists() and (logs / "query.pid").exists()


def test_r46lane_fu1_each_started_process_records_its_own_pid_not_a_wrapper_shell() -> None:
    script = APP_FROM_SOURCE.read_text(encoding="utf-8")
    assert "python_role" not in script, "a shell function runs in a subshell: its pid is the shell's"
    start = _function(APP_FROM_SOURCE, "start")
    assert 'echo "$!" >"$LOGS/$name.pid"' in start and 'rm -f "$LOGS/$name.exited"' in start
    assert "trap stop_all TERM INT" in script


def _depends_on(service: str) -> dict[str, dict]:
    """The service's depends_on as Compose merges the files: a map merged key by key, ``!reset`` emptying it."""
    merged: dict[str, dict] = {}
    for path in MATRIX_FILES:
        spec = compose_services(path).get(service)
        if spec is None or "depends_on" not in spec:
            continue
        value = spec["depends_on"]
        if value is None:
            merged = {}
        elif isinstance(value, list):
            merged.update({name: {} for name in value})
        else:
            merged.update({name: dict(v or {}) for name, v in value.items()})
    return merged


def _stack_profile(stack: str) -> dict[str, str]:
    script = "\n".join(('die() { echo "$*" >&2; exit 1; }', _function(RUN_SH, "stack_profile_env"), "stack_profile_env"))
    done = subprocess.run(["bash", "-c", script], env={"E2E_STACK": stack, "PATH": os.environ.get("PATH", "")},
                          capture_output=True, text=True, check=True)
    return dict(line.split("=", 1) for line in done.stdout.splitlines())


def _stack_stores(tmp_path: Path, lines: dict[str, str]) -> list[tuple[str, str]]:
    env_file = tmp_path / "stack.env"
    env_file.write_text("".join(f"{k}={v}\n" for k, v in lines.items()), encoding="utf-8")
    script = "\n".join((
        "set -euo pipefail",
        'envv() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2- || true; }',
        _function(RUN_SH, "stack_stores"),
        "stack_stores",
    ))
    done = subprocess.run(["bash", "-c", script], env={"ENV_FILE": str(env_file), "PATH": os.environ.get("PATH", "")},
                          capture_output=True, text=True, check=True)
    return [tuple(line.split(" ", 1)) for line in done.stdout.splitlines()]  # type: ignore[misc]


@pytest.mark.parametrize("stack", ["ST-1", "ST-3", "ST-4", "ST-5", "dev-harness"])
def test_r46lane_st4_every_job_a_store_waits_for_is_run_before_it(tmp_path: Path, stack: str) -> None:
    listed = _stack_stores(tmp_path, _stack_profile(stack))
    jobs = [name for kind, name in listed if kind == "job"]
    stores = [name for kind, name in listed if kind == "store"]
    assert stores, listed
    services = compose_services(BASE_COMPOSE)
    for store in stores:
        for dep, spec in _depends_on(store).items():
            if spec.get("condition") == "service_completed_successfully":
                assert dep in jobs, f"{stack}: {store} waits for {dep}, which the store list does not run"
                assert listed.index(("job", dep)) < listed.index(("store", store))
    for job in jobs:
        restart = (services.get(job) or {}).get("restart")
        assert restart == "no", f"{job} is a one-shot job (restart: no), never waited healthy"


def test_r46lane_st4_kv_etcd_runs_etcd_tls_as_a_job_and_up_stack_waits_for_it_to_exit_0(tmp_path: Path) -> None:
    listed = _stack_stores(tmp_path, _stack_profile("ST-4"))
    assert ("job", "etcd-tls") in listed and ("store", "etcd") in listed
    up = _function(RUN_SH, "up_stack")
    assert up.index('run_stack_job "$name"') < up.index('compose start "${stores[@]}"'), "the jobs finish before the stores start"
    assert '"${jobs[@]}" "${stores[@]}"' in up, "the jobs are created with the stack"
    job = _function(RUN_SH, "run_stack_job")
    assert "docker wait" in job and '[[ "$code" == "0" ]]' in job
