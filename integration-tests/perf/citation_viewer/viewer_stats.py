"""The arithmetic behind the citation viewer benchmark: percentiles, budgets and the history check."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

#: Server budget for a 304 (milliseconds, p95), at 1 and at 16 clients.
NOT_MODIFIED_P95_BUDGET_MS = 50.0
#: A run more than this share above the median of recent history fails.
REGRESSION_SHARE = 0.10
#: How many earlier runs the median is taken over.
HISTORY_WINDOW = 8


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; None for no samples."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-int(pct * len(ordered)) // 100)) if pct < 100 else len(ordered)
    return ordered[min(rank, len(ordered)) - 1]


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def read_history(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def append_history(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def regressions(history: list[dict[str, Any]], current: dict[str, Any], keys: list[str]) -> list[str]:
    """The measures of ``current`` more than ``REGRESSION_SHARE`` above the median of the last runs."""
    recent = history[-HISTORY_WINDOW:]
    found = []
    for key in keys:
        earlier = [r[key] for r in recent if isinstance(r.get(key), (int, float))]
        base = median(earlier)
        value = current.get(key)
        if base is None or not isinstance(value, (int, float)) or base <= 0:
            continue
        if value > base * (1 + REGRESSION_SHARE):
            found.append(f"{key}: {value:.1f} > {base:.1f} + {int(REGRESSION_SHARE * 100)}%")
    return found


def over_budget(p95_by_clients: dict[int, float | None]) -> list[str]:
    out = []
    for clients, p95 in sorted(p95_by_clients.items()):
        if p95 is None:
            out.append(f"{clients} clients: no samples")
        elif p95 > NOT_MODIFIED_P95_BUDGET_MS:
            out.append(f"{clients} clients: 304 p95 {p95:.1f} ms > {NOT_MODIFIED_P95_BUDGET_MS:.0f} ms")
    return out
