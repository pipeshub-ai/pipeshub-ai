#!/usr/bin/env python3
"""PF-09: sandbox I/O cost of gVisor (``runsc``) against hardened runc, on this host's Docker daemon.

The method and the rule that decides the default are pre-registered in
``docs/agent-harness-v2/adr/ADR-OWD-03-isolation-and-tenancy.md`` ("PF-09 runtime benchmark"); this script runs
that method and prints the numbers and the rule's verdict. It creates only ``pxh-pf09-*`` volumes and containers
and removes them. Run ``run`` through ``$PXH_LANE`` when set.

    python3 -I scripts/pf09_runtime_bench.py setup --work /tmp/pxh-gve/pf09 --repo . --sha 274cbef85e
    python3 -I scripts/pf09_runtime_bench.py run --work /tmp/pxh-gve/pf09 --site <purelib>
    python3 -I scripts/pf09_runtime_bench.py clean --work /tmp/pxh-gve/pf09

Variants (R32-SECCOMP): ``--runtime`` names the daemon runtimes to sample (default ``runc`` and ``runsc``), and
``--runtime-label RUNTIME=LABEL`` records one under another name, so a daemon whose ``runsc`` an operator has just
registered with ``--oci-seccomp`` is sampled as ``runsc-oci-seccomp`` and reported next to the earlier ``runsc`` file:

    python3 -I scripts/pf09_runtime_bench.py run --work … --site … \\
        --runtime runc --runtime runsc --runtime-label runsc=runsc-oci-seccomp
    python3 -I scripts/pf09_runtime_bench.py report backend/python/tests/fixtures/pf09/samples-2026-11-12.jsonl new.jsonl

``report`` compares every label with ``runc`` by the pre-registered rule, and a ``runsc-*`` label with ``runsc`` too.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IMAGE = "pxh-opencode-tools:1.18.34-ph7"
# The engine image's Python is musl; the suite's compiled deps (pydantic-core) are glibc wheels, as run_code's are.
PYTEST_IMAGE = "pxh-sandbox:pr1"
IMAGE_SITE = "/usr/local/lib/python3.12/site-packages"
RUNTIMES = ("runc", "runsc")
BASELINE = "runc"
WORKLOADS = ("git_clone", "npm_ci", "pytest")
SAMPLES = 7
QUIET_CPU = 40
ROOM_RAM = 80
PYTEST_PATHS = (
    "tests/unit/harness_core/policy",
    "tests/unit/harness_core/budget",
    "tests/unit/harness_core/access",
    "tests/unit/harness_core/journal",
)
LOCKFILE_DIR = "backend/nodejs/apps"

# Runs inside the sandbox: each workload timed with the sandbox's own monotonic clock, so start-up is not in it.
DRIVER = r"""
import json, subprocess, sys, time
out = {}
def timed(name, argv, cwd):
    t = time.perf_counter()
    done = subprocess.run(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out[name] = time.perf_counter() - t
    if done.returncode:
        out[name + "_error"] = f"exit {done.returncode}: " + done.stdout.decode(errors="replace")[-1500:]
if sys.argv[1] == "engine":
    timed("git_clone", ["git", "clone", "--quiet", "file:///mirror", "/workspace/repo"], "/workspace")
    timed("npm_ci", ["node", "/opt/npm/bin/npm-cli.js", "ci", "--offline", "--ignore-scripts", "--no-audit",
                     "--no-fund", "--cache", "/npmcache", "--logs-dir", "/tmp/npm-logs"], "/workspace/repo/backend/nodejs/apps")
else:
    timed("pytest", [sys.executable, "-m", "pytest", *sys.argv[2:], "-m", "not docker and not slow and not integration",
                     "-p", "no:cacheprovider", "-o", "addopts=", "-q"], "/workspace/repo/backend/python")
print("PF09" + json.dumps(out))
"""


@dataclass(frozen=True)
class Verdict:
    rule: str
    io_ratio: float
    cold_start_delta_s: float


def nearest_rank(values: list[float], pct: float) -> float:
    """The nearest-rank percentile (p95 of seven samples is the slowest one)."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def summary(values: list[float]) -> dict[str, float]:
    return {"p50": nearest_rank(values, 50), "p95": nearest_rank(values, 95), "n": len(values)}


@dataclass(frozen=True)
class Variant:
    """A daemon runtime sampled under a label (the runtime's own name unless ``--runtime-label`` renames it)."""

    label: str
    runtime: str


def variants(runtimes: list[str] | None, labels: list[str] | None) -> tuple[Variant, ...]:
    """The ``--runtime``/``--runtime-label`` arguments as variants, in order; a label must name a sampled runtime."""
    named = list(dict.fromkeys(runtimes or RUNTIMES))
    renamed: dict[str, str] = {}
    for entry in labels or []:
        runtime, sep, label = entry.partition("=")
        if not sep or not runtime or not label:
            raise ValueError(f"--runtime-label {entry!r}: expected RUNTIME=LABEL")
        if runtime not in named:
            raise ValueError(f"--runtime-label {entry!r}: {runtime!r} is not one of --runtime {named}")
        renamed[runtime] = label
    chosen = tuple(Variant(renamed.get(r, r), r) for r in named)
    if len({v.label for v in chosen}) != len(chosen):
        raise ValueError(f"two variants share a label: {[v.label for v in chosen]}")
    return chosen


def verdict(
    p50: dict[str, dict[str, float]], conformance_ok: bool, candidate: str = "runsc", baseline: str = BASELINE
) -> Verdict:
    """The pre-registered rule: ``p50[label][workload]`` in seconds, ``cold_start`` among the workloads."""
    runc = sum(p50[baseline][w] for w in WORKLOADS)
    runsc = sum(p50[candidate][w] for w in WORKLOADS)
    ratio = runsc / runc
    delta = p50[candidate]["cold_start"] - p50[baseline]["cold_start"]
    if not conformance_ok or ratio > 3.0 or delta > 3.0:
        rule = "back_to_owner"
    elif ratio > 2.0 or delta > 1.0:
        rule = "keep_with_follow_up"
    else:
        rule = "keep_gvisor_default"
    return Verdict(rule, ratio, delta)


def _cpu_busy_percent(window: float = 1.0) -> float:
    def sample() -> tuple[int, int]:
        fields = [int(x) for x in Path("/proc/stat").read_text().split("\n", 1)[0].split()[1:]]
        idle = fields[3] + fields[4]
        return sum(fields), idle

    total0, idle0 = sample()
    time.sleep(window)
    total1, idle1 = sample()
    return 100 * (1 - (idle1 - idle0) / max(total1 - total0, 1))


def host_room() -> dict[str, float]:
    load1 = float(Path("/proc/loadavg").read_text().split()[0])
    meminfo = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
    total, available = (int(meminfo[k].split()[0]) for k in ("MemTotal", "MemAvailable"))
    cpu = round(_cpu_busy_percent())
    return {"cpu": cpu, "mem": round(100 * (total - available) / total), "load1": load1}


def wait_quiet() -> dict[str, float]:
    while True:
        room = host_room()
        if room["cpu"] < QUIET_CPU and room["mem"] < ROOM_RAM:
            return room
        time.sleep(10)


def docker(*argv: str, capture: bool = False) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(["docker", *argv], capture_output=capture, text=True, check=False)
    if done.returncode:
        raise RuntimeError(f"docker {argv[0]} exited {done.returncode}: {(done.stderr or '')[-800:]}")
    return done


def hardened(runtime: str) -> list[str]:
    """The broker's host config (``DockerSandboxBroker._host_config``) for a 2-CPU engine sandbox."""
    return [
        f"--runtime={runtime}", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--init",
        "--cpus=2", "--memory=4g", "--memory-swap=4g", "--pids-limit=1024", "--network=none",
        "--tmpfs=/tmp:rw,size=512m", "--label=pipeshub.harness.owner=r31gveng",
    ]


def _driven(runtime: str, image: str, mounts: list[str], env: list[str], *args: str) -> dict[str, float]:
    done = docker("run", "--rm", *hardened(runtime), *mounts, *env, "--entrypoint", "python3", image, "-c", DRIVER, *args,
                  capture=True)
    line = next(x for x in done.stdout.splitlines() if x.startswith("PF09"))
    result = json.loads(line[4:])
    errors = {k: v for k, v in result.items() if k.endswith("_error")}
    if errors:
        raise RuntimeError(f"{runtime}: {errors}")
    return result


def io_sample(runtime: str, work: Path, site: str) -> dict[str, float]:
    """Clone and install in the engine image, then the suite in the glibc sandbox image, on one fresh volume."""
    name = f"pxh-pf09-{uuid.uuid4().hex[:8]}"
    docker("volume", "create", "--label", "pipeshub.harness.owner=r31gveng", name, capture=True)
    workspace = ["-v", f"{name}:/workspace"]
    try:
        engine = _driven(
            runtime, IMAGE,
            [*workspace, "-v", f"{work / 'mirror.git'}:/mirror:ro", "-v", f"{work / 'npmcache'}:/npmcache:ro",
             "-v", f"{work / 'npm'}:/opt/npm:ro"],
            ["-e", "HOME=/tmp"], "engine",
        )
        suite = _driven(
            # The lane's site-packages in the image's own place, as a venv's are to its Python: every Python the
            # suite starts (its child processes too) finds them after the standard library.
            runtime, PYTEST_IMAGE, [*workspace, "-v", f"{site}:{IMAGE_SITE}:ro"],
            ["-e", "PYTHONPATH=/workspace/repo/backend/python", "-e", "HOME=/tmp", "-e", "TMPDIR=/tmp"],
            "suite", *PYTEST_PATHS,
        )
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", name], capture_output=True, check=False)
    return {**engine, **suite}


def cold_start(runtime: str) -> float:
    started = time.perf_counter()
    docker("run", "--rm", *hardened(runtime), "--entrypoint", "true", IMAGE, capture=True)
    return time.perf_counter() - started


def setup(args: argparse.Namespace) -> None:
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    mirror = work / "mirror.git"
    sha = subprocess.run(["git", "-C", args.repo, "rev-parse", f"{args.sha}^{{commit}}"],
                         capture_output=True, text=True, check=True).stdout.strip()
    if not mirror.exists():
        subprocess.run(["git", "init", "--quiet", "--bare", str(mirror)], check=True)
        subprocess.run(["git", "-C", str(mirror), "fetch", "--quiet", "--depth", "1",
                        f"file://{Path(args.repo).resolve()}", f"{sha}:refs/heads/main"], check=True)
        subprocess.run(["git", "-C", str(mirror), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
    npm_src = Path(shutil.which("npm") or "").resolve().parents[1]
    if not (work / "npm").exists():
        shutil.copytree(npm_src, work / "npm", symlinks=True)
    cache, staging = work / "npmcache", work / "npm-fill"
    if not cache.exists():
        staging.mkdir(exist_ok=True)
        for name in ("package.json", "package-lock.json"):
            body = subprocess.run(["git", "-C", args.repo, "show", f"{args.sha}:{LOCKFILE_DIR}/{name}"],
                                  capture_output=True, check=True).stdout
            (staging / name).write_bytes(body)
        subprocess.run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund", "--cache", str(cache)],
                       cwd=staging, check=True)
        shutil.rmtree(staging)
    # The sandbox user (uid 1000) reads the mirror, npm and its cache; nothing the sandbox writes lands in them.
    subprocess.run(["chmod", "-R", "a+rX", str(mirror), str(work / "npm"), str(cache)], check=False)


def run(args: argparse.Namespace) -> None:
    work = Path(args.work)
    out = Path(args.out or work / "samples.jsonl")
    chosen = variants(args.runtime, args.runtime_label)
    with out.open("a") as log:
        def record(row: dict[str, object], variant: Variant) -> None:
            row = {**row, "runtime": variant.label, **({"dockerRuntime": variant.runtime}
                                                        if variant.runtime != variant.label else {})}
            log.write(json.dumps(row) + "\n")
            log.flush()
            print(json.dumps(row), flush=True)

        for v in chosen:
            record({"kind": "warmup", "room": wait_quiet(), **io_sample(v.runtime, work, args.site)}, v)
        for i in range(args.samples):
            # Alternating the order, so host drift falls on every variant.
            for v in chosen if i % 2 == 0 else chosen[::-1]:
                room = wait_quiet()
                record({"kind": "io", "i": i, "room": room, **io_sample(v.runtime, work, args.site)}, v)
        for v in chosen:
            _ = cold_start(v.runtime)
        for i in range(args.samples):
            for v in chosen if i % 2 == 0 else chosen[::-1]:
                room = wait_quiet()
                record({"kind": "cold", "i": i, "room": room, "cold_start": cold_start(v.runtime)}, v)
    report(argparse.Namespace(samples_file=[str(out)], conformance_ok=args.conformance_ok))


def compare(rows: list[dict[str, Any]], conformance_ok: bool) -> dict[str, object]:
    """Each label's p50/p95, and every label against ``runc`` by the rule; a ``runsc-*`` variant also against
    ``runsc``, so the cost of an operator flag is its own number."""
    labels = list(dict.fromkeys(str(r["runtime"]) for r in rows))
    stats: dict[str, dict[str, dict[str, float]]] = {}
    for label in labels:
        stats[label] = {}
        for workload in (*WORKLOADS, "cold_start"):
            kind = "cold" if workload == "cold_start" else "io"
            values = [float(r[workload]) for r in rows if r["kind"] == kind and r["runtime"] == label]
            stats[label][workload] = summary(values)
    p50 = {label: {w: s["p50"] for w, s in stats[label].items()} for label in labels}
    rooms = [r["room"] for r in rows if r["kind"] != "warmup"]
    load = {k: {"max": max(r[k] for r in rooms), "p50": nearest_rank([r[k] for r in rooms], 50)}
            for k in ("cpu", "mem", "load1")}
    pairs = [(label, BASELINE) for label in labels if label != BASELINE and BASELINE in p50]
    pairs += [(label, "runsc") for label in labels if label.startswith("runsc-") and "runsc" in p50]
    compared = {
        f"{candidate}/{baseline}": {
            "ratios": {w: p50[candidate][w] / p50[baseline][w] for w in (*WORKLOADS, "cold_start")},
            "verdict": verdict(p50, conformance_ok, candidate, baseline).__dict__,
        }
        for candidate, baseline in pairs
    }
    return {"stats": stats, "compared": compared, "host": load}


def report(args: argparse.Namespace) -> None:
    files = args.samples_file if isinstance(args.samples_file, list) else [args.samples_file]
    rows = [json.loads(x) for f in files for x in Path(f).read_text().splitlines() if x.strip()]
    print(json.dumps(compare(rows, args.conformance_ok), indent=2))


def clean(args: argparse.Namespace) -> None:
    # An earlier run's sandbox (uid 1000, not this user) may have left files the host user cannot remove.
    subprocess.run(["docker", "run", "--rm", "--network=none", "--user=0", "-v", f"{Path(args.work).resolve()}:/w",
                    "--entrypoint", "sh", PYTEST_IMAGE, "-c", "rm -rf /w/* /w/.[!.]*"], capture_output=True, check=False)
    shutil.rmtree(args.work, ignore_errors=True)
    names = docker("volume", "ls", "-q", "--filter", "name=pxh-pf09-", capture=True).stdout.split()
    if names:
        docker("volume", "rm", "-f", *names, capture=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("setup")
    p.add_argument("--work", required=True)
    p.add_argument("--repo", required=True)
    p.add_argument("--sha", required=True)
    p = sub.add_parser("run")
    p.add_argument("--work", required=True)
    p.add_argument("--site", required=True, help="a Python 3.12 purelib holding pytest and the suite's deps")
    p.add_argument("--samples", type=int, default=SAMPLES)
    p.add_argument("--out")
    p.add_argument("--runtime", action="append", help="a daemon runtime to sample (repeat; default runc and runsc)")
    p.add_argument("--runtime-label", action="append", metavar="RUNTIME=LABEL",
                   help="record RUNTIME's samples as LABEL, e.g. runsc=runsc-oci-seccomp after an operator flag")
    p.add_argument("--conformance-failed", dest="conformance_ok", action="store_false")
    p = sub.add_parser("report")
    p.add_argument("samples_file", nargs="+", help="one or more samples files, read as one")
    p.add_argument("--conformance-failed", dest="conformance_ok", action="store_false")
    p = sub.add_parser("clean")
    p.add_argument("--work", required=True)
    args = parser.parse_args(argv)
    {"setup": setup, "run": run, "report": report, "clean": clean}[args.cmd](args)
    return 0


if __name__ == "__main__":
    os.umask(0o022)
    sys.exit(main())
