#!/usr/bin/env python3
"""Memory footprint of a running PipesHub Compose project (P0.9, test S1).

    footprint.py measure --project P --label eval-harness-off --out a.json
    footprint.py render a.json b.json > table.md
    footprint.py check a.json          # exit 1 when over the eval budget

``measure`` is read-only: ``docker stats`` for every container of the project
and ``/proc`` inside the app container for each supervised process. It never
starts, stops or restarts anything, so it is safe against someone's live
stack. Stdlib only, so CI and a laptop run it without a virtualenv.

Per process it records RSS and PSS. RSS double-counts pages that forked
workers share with their parent; PSS splits them, so PSS columns add up to
something comparable with the container's cgroup usage.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# The `desktop-eval` budget: app ≤ 4.5 GB, total ≤ 7.5 GB on an 8 GB Docker VM.
EVAL_TOTAL_BUDGET_MB = 7680
EVAL_APP_BUDGET_MB = 4608
APP_SERVICE = "pipeshub-ai"

# Order matters: the first pattern found in a command line names the process.
PROCESS_RULES: tuple[tuple[str, str], ...] = (
    ("app.harness_main", "harness-worker"),
    ("app.model_gateway_main", "model-gateway"),
    ("app.query_main", "query"),
    ("app.connectors_main", "connectors"),
    ("app.indexing_main", "indexing"),
    ("app.docling_main", "docling"),
    ("app.embedding_main", "embedding"),
    ("app.parsing_main", "parsing"),
    ("app.extraction_main", "extraction"),
    ("slack-bot", "slack-bot"),
    ("dist/index.js", "node-api"),
    ("process_monitor", "supervisor"),
)
HARNESS_PROCESSES = frozenset({"harness-worker", "model-gateway"})

_PROC_SCRIPT = r"""
import os
for pid in os.listdir('/proc'):
    if not pid.isdigit():
        continue
    try:
        with open(f'/proc/{pid}/status') as fh:
            status = dict(l.split(':', 1) for l in fh if ':' in l)
        with open(f'/proc/{pid}/cmdline', 'rb') as fh:
            cmd = fh.read().replace(b'\0', b' ').decode(errors='replace').strip()
    except OSError:
        continue
    pss = ''
    try:
        with open(f'/proc/{pid}/smaps_rollup') as fh:
            for l in fh:
                if l.startswith('Pss:'):
                    pss = l.split()[1]
    except OSError:
        pass
    rss = status.get('VmRSS', '0 kB').split()[0]
    print(pid, status['PPid'].strip(), rss, pss or '-', cmd[:300], sep='\t')
"""

_UNITS = {
    "b": 1,
    "kb": 1000,
    "kib": 1024,
    "mb": 1000**2,
    "mib": 1024**2,
    "gb": 1000**3,
    "gib": 1024**3,
    "tb": 1000**4,
    "tib": 1024**4,
}


def parse_size_mb(text: str) -> float:
    """``docker stats`` sizes (``1.5GiB``, ``512MiB``, ``0B``) in MiB."""
    m = re.fullmatch(r"\s*([\d.]+)\s*([A-Za-z]+)\s*", text)
    if not m:
        raise ValueError(f"unrecognised size {text!r}")
    return float(m.group(1)) * _UNITS[m.group(2).lower()] / 1024**2


def classify(cmd: str) -> str | None:
    for needle, name in PROCESS_RULES:
        if needle in cmd:
            return name
    return None


def group_processes(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """Sum RSS/PSS per service; unnamed children join their nearest named ancestor."""
    by_pid = {r["pid"]: r for r in rows}

    def owner(pid: int, seen: frozenset[int] = frozenset()) -> str:
        row = by_pid.get(pid)
        if row is None or pid in seen:
            return "other"
        return classify(row["cmd"]) or owner(row["ppid"], seen | {pid})

    out: dict[str, dict[str, float]] = {}
    for r in rows:
        name = owner(r["pid"])
        slot = out.setdefault(name, {"rss_mb": 0.0, "pss_mb": 0.0, "processes": 0})
        slot["rss_mb"] += r["rss_kb"] / 1024
        slot["pss_mb"] += (r["pss_kb"] if r["pss_kb"] is not None else r["rss_kb"]) / 1024
        slot["processes"] += 1
    return out


def parse_proc_listing(text: str) -> list[dict[str, Any]]:
    rows = []
    for line in text.splitlines():
        parts = line.split("\t", 4)
        if len(parts) < 4:
            continue
        pid, ppid, rss, pss = parts[:4]
        rows.append(
            {
                "pid": int(pid),
                "ppid": int(ppid),
                "rss_kb": int(rss),
                "pss_kb": None if pss == "-" else int(pss),
                "cmd": parts[4] if len(parts) > 4 else "",
            }
        )
    return rows


def _run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def _machine() -> dict[str, Any]:
    info = json.loads(_run("docker", "info", "--format", "{{json .}}"))
    return {
        "host_arch": platform.machine(),
        "host_os": platform.system(),
        "docker_os": info.get("OperatingSystem"),
        "docker_arch": info.get("Architecture"),
        "docker_vm_ram_mb": round(info.get("MemTotal", 0) / 1024**2),
        "docker_cpus": info.get("NCPU"),
        "docker_version": info.get("ServerVersion"),
    }


def measure(project: str, label: str, samples: int, interval: float) -> dict[str, Any]:
    ids = _run(
        "docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}"
    ).split()
    if not ids:
        raise SystemExit(f"footprint: no running containers in project {project!r}")
    services: dict[str, str] = {}
    for cid in ids:
        services[cid[:12]] = _run(
            "docker", "inspect", cid, "--format",
            '{{index .Config.Labels "com.docker.compose.service"}}',
        ).strip()
    app_id = next((c for c, s in services.items() if s == APP_SERVICE), None)

    # Several samples, keep the peak: a single idle reading can land between GCs.
    containers: dict[str, float] = {}
    processes: dict[str, dict[str, float]] = {}
    for i in range(samples):
        if i:
            time.sleep(interval)
        stats = _run("docker", "stats", "--no-stream", "--format", "{{json .}}", *ids)
        for line in stats.splitlines():
            row = json.loads(line)
            name = services.get(row["ID"][:12], row["Name"])
            used = parse_size_mb(row["MemUsage"].split("/")[0])
            containers[name] = max(containers.get(name, 0.0), used)
        if app_id:
            listing = _run("docker", "exec", app_id, "python3", "-c", _PROC_SCRIPT)
            for name, v in group_processes(parse_proc_listing(listing)).items():
                prev = processes.get(name)
                if prev is None or v["pss_mb"] > prev["pss_mb"]:
                    processes[name] = v

    image = ""
    if app_id:
        image = _run("docker", "inspect", app_id, "--format", "{{.Config.Image}} {{.Image}}").strip()
    return {
        "label": label,
        "project": project,
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "machine": _machine(),
        "app_image": image,
        "samples": samples,
        "containers_mb": {k: round(v, 1) for k, v in sorted(containers.items())},
        "app_processes": {
            k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in sorted(processes.items())
        },
    }


def totals(result: dict[str, Any]) -> dict[str, float]:
    c = result["containers_mb"]
    procs = result.get("app_processes", {})
    harness = sum(v["pss_mb"] for k, v in procs.items() if k in HARNESS_PROCESSES)
    return {
        "total_mb": round(sum(c.values()), 1),
        "app_mb": round(c.get(APP_SERVICE, 0.0), 1),
        "harness_pss_mb": round(harness, 1),
    }


def check(result: dict[str, Any], total_budget: float, app_budget: float) -> list[str]:
    t = totals(result)
    failures = []
    if t["total_mb"] > total_budget:
        failures.append(f"total {t['total_mb']:.0f} MiB > budget {total_budget:.0f} MiB")
    if t["app_mb"] > app_budget:
        failures.append(f"app container {t['app_mb']:.0f} MiB > budget {app_budget:.0f} MiB")
    return failures


def render(results: list[dict[str, Any]]) -> str:
    labels = [r["label"] for r in results]
    head = "| {} | {} |\n| --- | {} |\n".format
    out = []

    names = sorted({n for r in results for n in r["containers_mb"]})
    out.append("Containers (`docker stats` memory usage, MiB, peak of samples)\n")
    out.append(head("Container", " | ".join(labels), " | ".join("---:" for _ in labels)))
    for n in names:
        cells = [f"{r['containers_mb'].get(n, 0):,.0f}" if n in r["containers_mb"] else "—" for r in results]
        out.append(f"| `{n}` | {' | '.join(cells)} |\n")
    total_cells = " | ".join("**{:,.0f}**".format(totals(r)["total_mb"]) for r in results)
    out.append(f"| **total** | {total_cells} |\n\n")

    procs = sorted({n for r in results for n in r.get("app_processes", {})})
    out.append("Processes inside `pipeshub-ai` (PSS / RSS, MiB; children counted with their parent)\n")
    out.append(head("Process", " | ".join(labels), " | ".join("---:" for _ in labels)))
    for n in procs:
        cells = []
        for r in results:
            v = r.get("app_processes", {}).get(n)
            cells.append(f"{v['pss_mb']:,.0f} / {v['rss_mb']:,.0f}" if v else "—")
        out.append(f"| `{n}` | {' | '.join(cells)} |\n")
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--project", required=True)
    m.add_argument("--label", required=True)
    m.add_argument("--samples", type=int, default=3)
    m.add_argument("--interval", type=float, default=10.0)
    m.add_argument("--out", type=Path, required=True)
    r = sub.add_parser("render")
    r.add_argument("results", type=Path, nargs="+")
    c = sub.add_parser("check")
    c.add_argument("result", type=Path)
    c.add_argument("--total-budget-mb", type=float, default=EVAL_TOTAL_BUDGET_MB)
    c.add_argument("--app-budget-mb", type=float, default=EVAL_APP_BUDGET_MB)
    args = p.parse_args(argv)

    if args.cmd == "measure":
        result = measure(args.project, args.label, args.samples, args.interval)
        args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(render([result]))
        return 0
    if args.cmd == "render":
        print(render([json.loads(f.read_text(encoding="utf-8")) for f in args.results]))
        return 0
    result = json.loads(args.result.read_text(encoding="utf-8"))
    failures = check(result, args.total_budget_mb, args.app_budget_mb)
    t = totals(result)
    print(
        f"footprint[{result['label']}]: total {t['total_mb']:.0f} MiB "
        f"(budget {args.total_budget_mb:.0f}), app {t['app_mb']:.0f} MiB "
        f"(budget {args.app_budget_mb:.0f})"
    )
    for f in failures:
        print(f"footprint: over budget: {f}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
