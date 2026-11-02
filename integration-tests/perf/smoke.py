"""The weekly perf smoke: one small run of the indexing and query benchmarks, judged against a budget.

It fails only when an operation's p95 is above `factor` times the committed baseline, or when
anything errored. With no baseline file it reports and passes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

SMOKE = {"docs": 50, "query_docs": 20, "users": 1, "duration": 60}
BASELINES = Path(__file__).resolve().parent / "baselines"
OPERATIONS = ("index p95", "search p95", "chat p95")


def summarize(indexing: dict[str, Any], query: dict[str, Any]) -> dict[str, float | None]:
    """The numbers the budget judges, read from the two benchmark results."""
    def p95(op: str) -> float | None:
        return (((query["metrics"].get("operations") or {}).get(op) or {}).get("latency_seconds") or {}).get("p95")

    idx = indexing["metrics"]
    index_errors = (idx.get("failures") or {}).get("total", 0) / max(idx.get("records_uploaded") or 0, 1)
    return {
        "index p95": (idx.get("time_to_indexed_seconds") or {}).get("p95"),
        "search p95": p95("search"),
        "chat p95": p95("chat"),
        "error_rate": max(index_errors, query["metrics"].get("error_rate") or 0),
    }


def budget_failures(result: dict[str, Any], baseline: dict[str, Any], factor: float = 2.0) -> list[str]:
    out = []
    for op in OPERATIONS:
        now, was = result.get(op), baseline.get(op)
        if now is not None and was and now > factor * was:
            out.append(f"{op}: {now}s is above {factor:g}x the baseline {was}s")
    if (result.get("error_rate") or 0) > 0:
        out.append(f"error rate {result['error_rate']} (must be 0)")
    return out


def load_baseline(label: str) -> dict[str, Any]:
    """perf/baselines/smoke-<label>.json (a label that already starts with `smoke-` is not prefixed twice), or {}."""
    import json

    name = label if label.startswith("smoke-") else f"smoke-{label}"
    path = BASELINES / f"{name}.json"
    if not path.is_file():
        print("no baseline: report only")
        return {}
    return json.loads(path.read_text(encoding="utf-8"))
