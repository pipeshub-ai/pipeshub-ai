"""A/B comparison of two runs.

The verdict is deliberately conservative. A difference only counts when it is
larger than the noise both runs demonstrated about themselves, so a 4% "win"
between two runs that each wobble 6% is reported as inconclusive rather than
dressed up as a result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .report import CSS, _esc, line_chart


def load(directory: Path) -> dict[str, Any]:
    path = directory / "result.json"
    if not path.exists():
        raise FileNotFoundError(f"no result.json in {directory} — is that a completed run directory?")
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["_dir"] = str(directory)
    return doc


def _delta(baseline: float, candidate: float) -> tuple[float, str]:
    if baseline == 0:
        return 0.0, "n/a"
    change = (candidate - baseline) / baseline
    return change, f"{change:+.1%}"


def verdict(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    a, b = baseline.get("summary") or {}, candidate.get("summary") or {}
    if not a or not b:
        return {"state": "unknown", "reason": "one of the runs has no measured iterations"}

    blockers: list[str] = []
    for name, doc, summary in (("baseline", baseline, a), ("candidate", candidate, b)):
        if summary.get("indexing_leaked"):
            blockers.append(f"{name} indexed records despite enable_manual_sync — it is not connector-only")
        if not doc.get("source", {}).get("deterministic", True):
            blockers.append(f"{name} used a non-deterministic (SaaS-backed) load source")
        if not summary.get("all_complete", True):
            blockers.append(f"{name} had an iteration that timed out before completing")
        if summary.get("records_diverged"):
            # Duplicated work inflates records_per_s, so comparing against it
            # would read a correctness regression as a throughput improvement.
            blockers.append(
                f"{name} had connectors disagree on their record count — it duplicated work"
            )
    if baseline.get("scenario", {}).get("source") != candidate.get("scenario", {}).get("source"):
        blockers.append("the two runs used different load sources")
    for key in ("units", "scale", "seed"):
        if baseline.get("scenario", {}).get(key) != candidate.get("scenario", {}).get(key):
            blockers.append(f"the two runs used different `{key}` — the corpora are not the same")

    if blockers:
        return {"state": "invalid", "reason": "; ".join(blockers)}

    change, label = _delta(a["records_per_s"]["median"], b["records_per_s"]["median"])
    # Combined noise floor: a difference must clear what both runs proved they
    # wobble by on their own before it counts as signal.
    noise = a["records_per_s"]["cv"] + b["records_per_s"]["cv"]
    if abs(change) <= noise:
        return {
            "state": "inconclusive",
            "reason": (
                f"throughput moved {label}, within the combined run-to-run noise of {noise:.1%}"
            ),
            "change": change,
            "noise": noise,
        }
    return {
        "state": "better" if change > 0 else "worse",
        "reason": f"throughput {label} against a combined noise floor of {noise:.1%}",
        "change": change,
        "noise": noise,
    }


ROWS = [
    ("throughput (rec/s, median)", lambda s: s["records_per_s"]["median"], "higher", "{:.2f}"),
    ("run-to-run spread (cv)", lambda s: s["records_per_s"]["cv"], "lower", "{:.1%}"),
    ("sync duration (s, median)", lambda s: s["duration_s"]["median"], "lower", "{:.1f}"),
    ("API p99 (ms, median)", lambda s: s["api_p99_ms"]["median"], "lower", "{:.0f}"),
    ("API p99 (ms, worst)", lambda s: s["api_p99_ms"]["max"], "lower", "{:.0f}"),
    ("longest API stall (s, median)", lambda s: s["api_max_gap_s"]["median"], "lower", "{:.1f}"),
    ("longest API stall (s, worst)", lambda s: s["api_max_gap_s"]["max"], "lower", "{:.1f}"),
]


def render(baseline: dict[str, Any], candidate: dict[str, Any]) -> str:
    call = verdict(baseline, candidate)
    banner_class = {
        "better": "ok",
        "worse": "bad",
        "inconclusive": "warn",
        "invalid": "bad",
        "unknown": "warn",
    }[call["state"]]

    a, b = baseline.get("summary") or {}, candidate.get("summary") or {}
    rows = []
    if a and b:
        for label, pick, direction, fmt in ROWS:
            base_v, cand_v = pick(a), pick(b)
            change, change_label = _delta(base_v, cand_v)
            improved = change < 0 if direction == "lower" else change > 0
            cls = "good" if improved else ("bad" if change else "muted")
            rows.append(
                f"<tr><td>{_esc(label)}</td><td>{fmt.format(base_v)}</td>"
                f"<td>{fmt.format(cand_v)}</td>"
                f'<td class="{cls}">{_esc(change_label)}</td></tr>'
            )

    charts = ""
    series: dict[str, list[tuple[float, float]]] = {}
    for name, doc in (("baseline", baseline), ("candidate", candidate)):
        measured = [it for it in doc.get("iterations", []) if it.get("kind") == "measured"]
        if not measured:
            continue
        ranked = sorted(measured, key=lambda it: it["throughput"]["records_per_s"])
        median_iter = ranked[len(ranked) // 2]
        merged: dict[float, float] = {}
        for pts in median_iter["throughput"]["series"].values():
            for t, total in pts:
                merged[t] = merged.get(t, 0.0) + total
        series[name] = sorted(merged.items())
    if series:
        charts = "<h2>Records discovered over time (median iteration)</h2>" + line_chart(
            series, y_label="records"
        )

    return (
        "<title>A/B comparison</title>"
        f"<style>{CSS}</style>"
        "<h1>A/B comparison</h1>"
        f'<div class="sub"><b>baseline</b> {_esc(baseline.get("run_id"))} &middot; '
        f'<b>candidate</b> {_esc(candidate.get("run_id"))}</div>'
        f'<div class="banner {banner_class}"><b>{call["state"].upper()}.</b> {_esc(call["reason"])}</div>'
        '<div class="scroll"><table><thead><tr><th>metric</th><th>baseline</th><th>candidate</th>'
        "<th>change</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
        + charts
    )


def write(baseline_dir: Path, candidate_dir: Path, out: Path) -> Path:
    baseline, candidate = load(baseline_dir), load(candidate_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(baseline, candidate), encoding="utf-8")
    return out
