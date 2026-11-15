"""process_monitor.sh waits for a slow service that is making progress and still gives up on a dead or hung one."""

import contextlib
import os
import pathlib
import re
import signal
import subprocess
import time

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[5] / "deployment" / "process_monitor.sh"


def _functions() -> str:
    text = SCRIPT.read_text()
    parts = []
    for name in ("log", "check_startup_limits", "cpu_ticks", "wait_ready", "await_services"):
        m = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.S | re.M)
        assert m, f"{name} not found in process_monitor.sh"
        parts.append(m.group(0))
    limits = re.findall(r"^STARTUP_\w+=.*$", text, re.M)
    assert len(limits) == 3, "startup limits not found in process_monitor.sh"
    return "\n".join(limits + parts)  # the limits first: check_startup_limits is only defined, not called


def _bash(script: str, env: dict[str, str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    """Run *script* in its own session and kill the whole group afterwards.

    The workloads are busy loops, some with children of their own; killing only the PID the script
    knows about leaves those running, and a test run would leave CPU burners behind on the runner.
    """
    timeout = kwargs.pop("timeout", 60)
    proc = subprocess.Popen(["bash", "-c", script], env=env, text=True, start_new_session=True, **kwargs)  # type: ignore[call-overload]
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)


def _run(tmp_path: pathlib.Path, workload: str, healthy_after: float, **limits: int) -> tuple[int, float, str]:
    """Run wait_ready against a workload process whose health URL answers (a flag file) after healthy_after s."""
    flag = tmp_path / "healthy"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(f'#!/bin/sh\n[ -e "{flag}" ]\n')
    curl.chmod(0o755)
    env_limits = " ".join(f"{k}={v}" for k, v in limits.items())
    script = f"""
LOG_FILE={tmp_path}/log
{env_limits}
{_functions()}
{workload} >/dev/null 2>&1 &
pid=$!
( sleep {healthy_after}; touch "{flag}" ) >/dev/null 2>&1 &
helper=$!
wait_ready svc $pid http://x/health
rc=$?
kill $pid $helper 2>/dev/null
exit $rc
"""
    began = time.monotonic()
    out = _bash(script, {"PATH": f"{bin_dir}:/usr/bin:/bin"}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return out.returncode, time.monotonic() - began, out.stdout


BUSY = "bash -c 'while :; do :; done'"


def test_a_busy_service_is_waited_for_past_the_stall_window(tmp_path: pathlib.Path) -> None:
    # Healthy only after 7 s of CPU work, with a 3 s stall window: a fixed window of that size would have killed it.
    rc, took, _ = _run(tmp_path, BUSY, 7, STARTUP_STALL_SECONDS=3, STARTUP_MAX_SECONDS=30, STARTUP_POLL_SECONDS=1)
    assert rc == 0
    assert took >= 6


def test_a_service_that_uses_no_cpu_is_given_up_on(tmp_path: pathlib.Path) -> None:
    rc, took, out = _run(tmp_path, "sleep 100", 1000, STARTUP_STALL_SECONDS=3, STARTUP_MAX_SECONDS=60, STARTUP_POLL_SECONDS=1)
    assert rc == 1
    assert took < 15
    assert "used no CPU" in out


def test_a_dead_service_fails_at_once(tmp_path: pathlib.Path) -> None:
    rc, took, out = _run(tmp_path, "true", 1000, STARTUP_STALL_SECONDS=60, STARTUP_MAX_SECONDS=60, STARTUP_POLL_SECONDS=1)
    assert rc == 1
    assert took < 8
    assert "exited before it became healthy" in out


def test_a_busy_service_is_still_bounded(tmp_path: pathlib.Path) -> None:
    rc, took, out = _run(tmp_path, BUSY, 1000, STARTUP_STALL_SECONDS=60, STARTUP_MAX_SECONDS=4, STARTUP_POLL_SECONDS=1)
    assert rc == 1
    assert 3 <= took < 15
    assert "not healthy after 4s" in out


def test_a_healthy_service_returns_immediately(tmp_path: pathlib.Path) -> None:
    rc, took, _ = _run(tmp_path, "sleep 100", 0, STARTUP_STALL_SECONDS=60, STARTUP_MAX_SECONDS=60, STARTUP_POLL_SECONDS=1)
    assert rc == 0
    assert took < 6


def test_all_python_services_are_spawned_before_any_is_waited_for() -> None:
    text = SCRIPT.read_text()
    startup = text[text.index("=== Process Monitor Starting"): text.index("Beginning monitoring cycle")]
    first_wait = startup.index("await_services")
    before = startup[:first_wait]
    for spawn in ("spawn_embedding", "spawn_connector", "start_query", "start_docling", "spawn_parsing", "spawn_extraction"):
        assert spawn in before, f"{spawn} must run before the first wait"
    # Indexing still starts only after Parsing and Extraction are healthy.
    assert startup.index("start_indexing") > startup.rindex("await_services")


def test_no_service_uses_the_fixed_retry_window_any_more() -> None:
    text = SCRIPT.read_text()
    assert "MAX_RETRIES=60" not in text and "MAX_RETRIES=120" not in text


def test_a_service_that_never_answers_cannot_hold_the_wait_past_the_limit(tmp_path: pathlib.Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        '#!/bin/bash\nm=""\nwhile [ $# -gt 0 ]; do [ "$1" = --max-time ] && m=$2; shift; done\n'
        '[ -n "$m" ] || m=300\nsleep "$m"\nexit 28\n'
    )
    curl.chmod(0o755)
    script = f"""
LOG_FILE={tmp_path}/log
STARTUP_STALL_SECONDS=60 STARTUP_MAX_SECONDS=3 STARTUP_POLL_SECONDS=1
{_functions()}
bash -c 'while :; do :; done' >/dev/null 2>&1 & busy=$!
wait_ready connector $busy http://x/slow
rc=$?
kill $busy 2>/dev/null
exit $rc
"""
    out = _bash(script, {"PATH": f"{bin_dir}:/usr/bin:/bin"}, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
    assert out.returncode == 1


def test_a_failed_service_is_seen_under_the_scripts_errexit(tmp_path: pathlib.Path) -> None:
    """Called where errexit applies (no ``||`` around it), a waiter that failed must still report it, or startup waits for ever."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(f'#!/bin/sh\nfor last; do :; done\n[ -e "{tmp_path}/${{last##*/}}" ]\n')
    curl.chmod(0o755)
    script = f"""
set -e
LOG_FILE={tmp_path}/log
STARTUP_STALL_SECONDS=60 STARTUP_MAX_SECONDS=60 STARTUP_POLL_SECONDS=1
{_functions()}
sleep 1 >/dev/null 2>&1 & crashing=$!
bash -c 'while :; do :; done' >/dev/null 2>&1 & slow=$!
await_services connector $slow http://x/slow embedding $crashing http://x/embedding
exit 0
"""
    began = time.monotonic()
    out = _bash(script, {"PATH": f"{bin_dir}:/usr/bin:/bin"}, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15)
    assert out.returncode == 1
    assert time.monotonic() - began < 8, "the failed waiter's status was lost"


def test_the_first_failed_service_ends_the_wait_without_waiting_for_the_others(tmp_path: pathlib.Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(f'#!/bin/sh\nfor last; do :; done\n[ -e "{tmp_path}/${{last##*/}}" ]\n')
    curl.chmod(0o755)
    script = f"""
LOG_FILE={tmp_path}/log
STARTUP_STALL_SECONDS=60 STARTUP_MAX_SECONDS=60 STARTUP_POLL_SECONDS=1
{_functions()}
sleep 2 >/dev/null 2>&1 & crashing=$!
bash -c 'while :; do :; done' >/dev/null 2>&1 & slow=$!
( sleep 15; touch {tmp_path}/slow ) >/dev/null 2>&1 & helper=$!
await_services connector $slow http://x/slow embedding $crashing http://x/embedding
rc=$?
kill $slow $helper 2>/dev/null
exit $rc
"""
    began = time.monotonic()
    out = _bash(script, {"PATH": f"{bin_dir}:/usr/bin:/bin"}, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert out.returncode == 1
    assert time.monotonic() - began < 8, "waited for the slow service after another one had failed"


def _limits(env: dict[str, str]) -> list[str]:
    assigns = "\n".join(f"{k}='{v}'" for k, v in env.items())
    # the extracted default lines run first, so the overrides go after them
    funcs = _functions()
    script = f"LOG_FILE=/dev/null\n{funcs}\n{assigns}\ncheck_startup_limits\necho $STARTUP_POLL_SECONDS $STARTUP_STALL_SECONDS $STARTUP_MAX_SECONDS"
    out = subprocess.run(["bash", "-c", script], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=True)
    return out.stdout.split()[-3:]


@pytest.mark.parametrize("bad", ["abc", "0", "-5", "", "1.5", "10s"])
def test_an_unusable_poll_interval_falls_back_to_the_default(bad: str) -> None:
    assert _limits({"STARTUP_POLL_SECONDS": bad}) == ["2", "120", "900"]


@pytest.mark.parametrize("bad", ["abc", "-1", "0", "1"])
def test_an_unusable_stall_window_falls_back_to_the_default(bad: str) -> None:
    # 1 is below the poll interval of 2
    assert _limits({"STARTUP_STALL_SECONDS": bad}) == ["2", "120", "900"]


@pytest.mark.parametrize("bad", ["abc", "-1", "0", "100"])
def test_an_unusable_overall_bound_falls_back_to_the_default(bad: str) -> None:
    # 100 is below the stall window of 120
    assert _limits({"STARTUP_MAX_SECONDS": bad}) == ["2", "120", "900"]


def test_usable_limits_are_kept() -> None:
    assert _limits({"STARTUP_POLL_SECONDS": "1", "STARTUP_STALL_SECONDS": "30", "STARTUP_MAX_SECONDS": "300"}) == ["1", "30", "300"]


def test_cpu_used_by_a_child_counts_as_progress(tmp_path: pathlib.Path) -> None:
    # The service process only waits; its child does the work.
    rc, _, _ = _run(tmp_path, "bash -c 'bash -c \"while :; do :; done\" & wait'", 7,
                    STARTUP_STALL_SECONDS=3, STARTUP_MAX_SECONDS=30, STARTUP_POLL_SECONDS=1)
    assert rc == 0


def test_a_parent_and_child_that_both_idle_still_stall(tmp_path: pathlib.Path) -> None:
    rc, _, out = _run(tmp_path, "bash -c 'sleep 100 & wait'", 1000,
                      STARTUP_STALL_SECONDS=3, STARTUP_MAX_SECONDS=60, STARTUP_POLL_SECONDS=1)
    assert rc == 1
    assert "used no CPU" in out


def test_the_real_startup_path_waits_for_a_busy_connector_past_the_old_window(tmp_path: pathlib.Path) -> None:
    """Runs the whole script with the time scaled down 20x and stand-ins for the services.

    The connector is busy for 10 s of real time, which is 200 s of script time: longer than the old fixed
    120 s window (60 polls), which ended the monitor, and far below the stall and overall limits.
    """
    app = tmp_path / "app"
    (app / "backend").mkdir(parents=True)
    (app / "python").mkdir()
    flags = tmp_path / "flags"
    flags.mkdir()
    shims = tmp_path / "bin"
    shims.mkdir()

    def shim(name: str, body: str) -> None:
        f = shims / name
        f.write_text("#!/bin/bash\n" + body)
        f.chmod(0o755)

    shim("sleep", 'exec /bin/sleep "$(awk "BEGIN{print $1/20}")"\n')
    shim("node", "exec /bin/sleep 1000\n")
    shim("python", f"""mod=${{2#app.}}; mod=${{mod%_main}}
if [ "$mod" = connectors ]; then end=$((SECONDS+10)); while [ $SECONDS -lt $end ]; do :; done; fi
touch {flags}/$mod
exec /bin/sleep 1000
""")
    shim("curl", f"""for last; do :; done
case $last in
  *:3000/*) exit 0 ;;
  *:8002/*) [ -e {flags}/embedding ] ;;
  *:8088/*) [ -e {flags}/connectors ] ;;
  *) exit 1 ;;
esac
""")
    script = tmp_path / "process_monitor.sh"
    script.write_text(SCRIPT.read_text().replace("/app", str(app)))
    log = app / "process_monitor.log"
    proc = subprocess.Popen(
        ["bash", str(script)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={"PATH": f"{shims}:/usr/bin:/bin", "PIPESHUB_SKIP_SLACKBOT": "true", "CHECK_INTERVAL": "20",
             "STARTUP_POLL_SECONDS": "1", "STARTUP_STALL_SECONDS": "120", "STARTUP_MAX_SECONDS": "900"},
    )
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and proc.poll() is None:
            if log.exists() and "All services started" in log.read_text():
                break
            time.sleep(0.5)
        started = log.exists() and "All services started" in log.read_text()
        assert started, f"monitor did not finish starting (exit {proc.poll()}):\n{log.read_text() if log.exists() else ''}"
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def test_the_limits_are_repaired_before_the_first_wait() -> None:
    # Kills: dropping the top-level check, which the tests above cannot see because they call it themselves.
    lines = SCRIPT.read_text().splitlines()
    checks = [i for i, line in enumerate(lines) if line.strip() == "check_startup_limits"]
    waits = [
        i for i, line in enumerate(lines)
        if re.match(r"\s*(?:if\s+!?\s*)?(?:await_services|wait_ready)\b(?!\(\))", line)
    ]
    assert checks, "check_startup_limits is never called"
    assert waits, "no startup wait found"
    assert checks[0] < waits[0]


def _alive_with(tag: str) -> list[int]:
    found = []
    for entry in pathlib.Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                if tag.encode() in (entry / "cmdline").read_bytes():
                    found.append(int(entry.name))
            except OSError:
                continue
    return found


def test_a_workload_and_its_children_do_not_outlive_the_test(tmp_path: pathlib.Path) -> None:
    # Kills: killing only the workload's PID, which left its busy child spinning after the test.
    tag = f"leakcheck{os.getpid()}x{time.monotonic_ns()}"
    rc, _, _ = _run(tmp_path, f"bash -c 'bash -c \"while :; do :; done # {tag}\" & wait'", 0,
                    STARTUP_STALL_SECONDS=3, STARTUP_MAX_SECONDS=30, STARTUP_POLL_SECONDS=1)
    assert rc == 0
    deadline = time.monotonic() + 5
    while _alive_with(tag) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert _alive_with(tag) == []
