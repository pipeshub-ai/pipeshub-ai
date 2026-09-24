"""Roll every completed run up into one comparable table.

The point of a long experiment session is that run N+1 is chosen by looking at
runs 1..N. That only works if the results and the profiles live in one place
instead of scrolling back through terminal output.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class Row:
    run_id: str
    source: str
    units: int
    scale: int
    rec_per_s: float
    cv: float
    reproducible: bool
    p99_ms: float
    stall_s: float
    cpu_mean: float
    rss_mb: float
    busy_s: float
    modules: dict[str, float]
    hot_frame: str
    valid: bool
    created_at: float

    @property
    def scenario(self) -> str:
        return f"{self.source}-{self.units}x{self.scale}"


def _connector_resources(doc: dict[str, Any]) -> tuple[float, float]:
    cpus, rss = [], []
    for it in doc.get("iterations", []):
        svc = (it.get("resources", {}).get("services") or {}).get("connector")
        if svc:
            cpus.append(svc.get("cpu_mean", 0.0))
            rss.append(svc.get("rss_max_mb", 0.0))
    return (max(cpus, default=0.0), max(rss, default=0.0))


def _backfill(directory: Path, profile: dict[str, Any]) -> None:
    """Populate modules/hot_frames/busy_s from the stored capture, in memory."""
    relative = profile.get("speedscope")
    if relative:
        candidate = directory / relative
    else:
        matches = sorted((directory / "profiles").glob("connector*.speedscope.json"))
        candidate = matches[0] if matches else directory / "profiles" / "connector.speedscope.json"
    if not candidate.exists():
        return
    try:
        from . import profiler

        doc = json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, ImportError):
        return
    modules, busy = profiler.module_breakdown(doc)
    profile["modules"] = modules
    profile["busy_s"] = busy
    profile["hot_frames"] = profiler.hot_app_frames(doc)


def load_row(directory: Path) -> Row | None:
    result = directory / "result.json"
    if not result.exists():
        return None
    try:
        doc = json.loads(result.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    summary = doc.get("summary") or {}
    if not summary:
        return None
    scenario = doc.get("scenario") or {}

    connector_profile = next(
        (p for p in doc.get("profiles", []) if p.get("service") == "connector"), {}
    )
    # Runs captured before the breakdown was recorded still have their raw
    # speedscope on disk; recompute rather than lose the history.
    if connector_profile and not connector_profile.get("modules"):
        _backfill(directory, connector_profile)
    hot = (connector_profile.get("hot_frames") or [{}])[0].get("frame", "")

    cpu, rss = _connector_resources(doc)
    return Row(
        run_id=doc.get("run_id", directory.name),
        source=scenario.get("source", "?"),
        units=scenario.get("units", 0),
        scale=scenario.get("scale", 0),
        rec_per_s=summary["records_per_s"]["median"],
        cv=summary["records_per_s"]["cv"],
        reproducible=bool(summary.get("reproducible")),
        p99_ms=summary["api_p99_ms"]["median"],
        stall_s=summary["api_max_gap_s"]["max"],
        cpu_mean=cpu,
        rss_mb=rss,
        busy_s=connector_profile.get("busy_s", 0.0),
        modules=connector_profile.get("modules") or {},
        hot_frame=hot,
        valid=(
            not summary.get("indexing_leaked")
            and not summary.get("records_diverged")
            and bool(summary.get("all_complete", True))
        ),
        created_at=doc.get("created_at", 0.0),
    )


def collect(runs_dir: Path) -> list[Row]:
    rows = [load_row(d) for d in sorted(runs_dir.iterdir()) if d.is_dir()]
    return [r for r in rows if r is not None]


def render_text(rows: list[Row]) -> str:
    if not rows:
        return "no completed runs found"
    out = [
        f"{'run':<26}{'scenario':<18}{'rec/s':>8}{'cv':>7}{'p99ms':>7}{'stall':>7}"
        f"{'cpu%':>6}{'rss':>7}{'busy':>6}  top modules",
        "-" * 132,
    ]
    for r in sorted(rows, key=lambda x: (x.source, x.units, x.created_at)):
        flag = "" if r.valid else "  !INVALID"
        repro = "" if r.reproducible else "?"
        mods = ", ".join(f"{m} {p:.0f}%" for m, p in list(r.modules.items())[:3])
        out.append(
            f"{r.run_id[:25]:<26}{r.scenario:<18}{r.rec_per_s:>8.2f}"
            f"{r.cv:>6.1%}{repro:<1}{r.p99_ms:>7.0f}{r.stall_s:>7.1f}"
            f"{r.cpu_mean:>6.0f}{r.rss_mb:>7.0f}{r.busy_s:>6.0f}  {mods}{flag}"
        )
    out.append("")
    out.append("cv marked ? = not reproducible (single iteration, or above threshold)")
    return "\n".join(out)


def render_pairs(rows: list[Row]) -> str:
    """before-X / after-X deltas, the experiment record."""
    by_id = {r.run_id: r for r in rows}
    lines: list[str] = []
    for row in sorted(rows, key=lambda x: x.run_id):
        if not row.run_id.startswith("before-"):
            continue
        after = by_id.get("after-" + row.run_id[len("before-"):])
        if after is None:
            continue
        change = ((after.rec_per_s - row.rec_per_s) / row.rec_per_s * 100) if row.rec_per_s else 0.0
        noise = (row.cv + after.cv) * 100
        verdict = "better" if change > noise else ("worse" if change < -noise else "inconclusive")
        lines.append(
            f"  {row.run_id[len('before-'):]:<14} {row.rec_per_s:>7.2f} -> {after.rec_per_s:>7.2f}"
            f"  {change:>+7.1f}%  (noise {noise:.1f}%)  {verdict}"
        )
    if not lines:
        return ""
    return "\nbefore/after pairs\n" + "\n".join(lines)
