"""Run a command in a cgroup scope with a CPU quota and a memory cap, and report what it used.

    python -m tests.perf.parse_bench.scope_run --cpus 2 --memory 4G --report out.json -- CMD ...

The tier cells of PX-3 (L-8: 4 CPUs, L-16: 8 CPUs, cell S: the parse service at 2 CPUs / 4 GiB) are
emulated with ``systemd-run --user --scope -p CPUQuota=N00% -p MemoryMax=M -p MemorySwapMax=0``:
swap is off so it cannot hide an out-of-memory. The quota and the cap apply to the whole process tree
(the harness and its parse workers). cpuset pinning alone is not accepted for a gate that involves
throttling or ``memory.peak``.

The wrapper runs *inside* the scope (``--inner``) so it can read the cgroup's counters after the
command exits and before the scope is torn down: ``memory.peak`` and the **anon** line of
``memory.stat`` side by side (page cache inflates ``memory.peak``), ``cpu.stat`` throttling
(``nr_throttled``, ``throttled_usec``), ``memory.events`` (``oom_kill``), plus wall time and the
command's exit code. Stated limit: the scope models the compose CPU quota and memory cap, not a Docker
Desktop VM's vCPU count.

Run the harness lean: nothing else of yours should be inside the scope. Contention rule (PX-2a): host
CPU < 70 % and loadavg < 4 before a run; re-take a run with foreign CPU above 15 % of quota x wall.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

CGROUP_ROOT = Path("/sys/fs/cgroup")


def own_cgroup() -> Path:
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        if line.startswith("0::"):
            return CGROUP_ROOT / line[3:].lstrip("/")
    raise RuntimeError("no cgroup v2 entry for this process")


def _kv(path: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    try:
        for line in path.read_text().splitlines():
            key, _, value = line.partition(" ")
            if value.strip().lstrip("-").isdigit():
                out[key] = int(value)
    except OSError:
        pass
    return out


def _int(path: Path) -> int | None:
    try:
        text = path.read_text().strip()
        return int(text) if text.isdigit() else None
    except OSError:
        return None


def read_counters(cg: Path) -> dict[str, object]:
    stat = _kv(cg / "memory.stat")
    cpu = _kv(cg / "cpu.stat")
    events = _kv(cg / "memory.events")
    return {
        "cgroup": str(cg),
        "cpu_max": (cg / "cpu.max").read_text().strip() if (cg / "cpu.max").exists() else None,
        "memory_max": _int(cg / "memory.max") if (cg / "memory.max").read_text().strip() != "max" else "max",
        "memory_peak_bytes": _int(cg / "memory.peak"),
        "anon_bytes_at_exit": stat.get("anon"),
        "file_bytes_at_exit": stat.get("file"),
        "nr_throttled": cpu.get("nr_throttled"),
        "throttled_usec": cpu.get("throttled_usec"),
        "cpu_usage_usec": cpu.get("usage_usec"),
        "oom_kill": events.get("oom_kill"),
        "oom": events.get("oom"),
    }


def _partial_path(report: Path) -> Path:
    return report.with_name(report.name + ".partial")


def inner(report: Path, command: list[str], sample_s: float = 0.05) -> int:
    cg = own_cgroup()
    anon_max = 0
    done = threading.Event()

    partial = _partial_path(report)

    def sample() -> None:
        # Also leaves ``<report>.partial`` for the outer process: an OOM kill takes this wrapper down with
        # the command, and the last sample (memory.peak, anon, memory.events) is then the only evidence.
        nonlocal anon_max
        last_write = 0.0
        while not done.wait(sample_s):
            anon_max = max(anon_max, _kv(cg / "memory.stat").get("anon", 0))
            now = time.monotonic()
            if now - last_write >= 0.25:
                last_write = now
                try:
                    snap = {**read_counters(cg), "anon_bytes_max_sampled": anon_max}
                    tmp = partial.with_name(partial.name + ".tmp")
                    tmp.write_text(json.dumps(snap))
                    tmp.replace(partial)
                except (OSError, ValueError):
                    pass

    sampler = threading.Thread(target=sample, daemon=True)
    sampler.start()
    started = time.monotonic()
    code = subprocess.call(command)
    wall = time.monotonic() - started
    done.set()
    sampler.join(1)
    out = read_counters(cg)
    # memory.peak is the scope's high-water mark and includes page cache; anon_bytes_max is the largest
    # anonymous memory seen by a 50 ms sampler (a lower bound of the true anon peak), anon at exit is
    # after the work ended.
    out.update(exit_code=code, wall_s=round(wall, 3), anon_bytes_max_sampled=anon_max, command=command)
    report.write_text(json.dumps(out, indent=1) + "\n")
    return code


def build_command(cpus: float, memory: str, report: Path, command: list[str]) -> list[str]:
    return [
        "systemd-run", "--user", "--scope", "--collect", "--quiet",
        "-p", f"CPUQuota={int(round(cpus * 100))}%",
        "-p", f"MemoryMax={memory}",
        "-p", "MemorySwapMax=0",
        sys.executable, "-m", "tests.perf.parse_bench.scope_run",
        "--inner", "--report", str(report), "--", *command,
    ]


OOM_SCOPE_EXIT = 241  # what ``systemd-run`` exits with when the scope was OOM-killed ("Failed with result 'oom-kill'")


def run_scope(
    cpus: float,
    memory: str,
    report: Path,
    command: list[str],
    launcher: Callable[[float, str, Path, list[str]], list[str]] = build_command,
) -> int:
    """Launch the scope and make sure the report on disk describes *this* run, or says it failed.

    The report is removed first, so a file left by an earlier run can never be read as this one's. The
    wrapper that writes it runs inside the scope, so an OOM kill of the whole scope takes it down with
    the command: no report is written. That is recorded here, by the outer process, as a failed run
    (``failed_run``, the scope's exit code, ``oom_suspected`` for 241) and the exit code is never 0.
    A missing report with exit code 0 is also a failed run.
    """
    report.unlink(missing_ok=True)
    _partial_path(report).unlink(missing_ok=True)
    rc = subprocess.call(launcher(cpus, memory, report, command), env=os.environ)
    killed = rc == OOM_SCOPE_EXIT or rc < 0  # 241 from systemd-run, or the wrapper itself died on a signal
    last_sample = None
    with contextlib.suppress(OSError, ValueError):
        last_sample = json.loads(_partial_path(report).read_text())
    _partial_path(report).unlink(missing_ok=True)
    if report.exists():
        try:
            data = json.loads(report.read_text())
        except ValueError:
            data = {"report_unreadable": True}
        data["scope_exit_code"] = rc
        data["failed_run"] = rc != 0 or bool(data.get("report_unreadable"))
    else:
        data = {
            "failed_run": True,
            "report_missing": True,
            "exit_code": rc,
            "scope_exit_code": rc,
            # The whole scope was killed: by the memory cap in practice (confirm with
            # ``journalctl --user -u <scope>``). ``oom_kill`` is the last memory.events count sampled
            # before it died, else 1 for the 241 exit.
            "killed": killed,
            "oom_suspected": killed,
            "oom_kill": ((last_sample or {}).get("oom_kill") or (1 if rc == OOM_SCOPE_EXIT else None)),
            "last_sample": last_sample,
            "command": command,
        }
    report.write_text(json.dumps(data, indent=1) + "\n")
    return rc if rc != 0 else (1 if data["failed_run"] else 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cpus", type=float, default=2.0)
    ap.add_argument("--memory", default="4G", help="MemoryMax, e.g. 4G (cell S) or 8G")
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--inner", action="store_true", help="internal: already inside the scope")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    args = ap.parse_args(argv)
    command = args.command[1:] if args.command and args.command[0] == "--" else args.command
    if not command:
        ap.error("a command is required after --")
    if args.inner:
        return inner(args.report, command)
    load = float(Path("/proc/loadavg").read_text().split()[0])
    if load > 4:
        print(f"warning: loadavg {load} > 4 (PX-2a contention rule); re-take if foreign CPU is high", file=sys.stderr)
    env_ok = subprocess.run(["systemd-run", "--version"], capture_output=True).returncode == 0
    if not env_ok:
        print("systemd-run is not available: no cgroup scope, nothing is claimed", file=sys.stderr)
        return 2
    return run_scope(args.cpus, args.memory, args.report, command)


if __name__ == "__main__":
    raise SystemExit(main())
