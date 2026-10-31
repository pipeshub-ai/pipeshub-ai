"""Split a plan's stack tests across parallel stack jobs, balanced by measured minutes.

A group never splits: one connector marker with measured minutes (its suite
shares sync state), else one test file (ordered lifecycle modules run whole).
The minutes are the ones scripts/shard_balance.py balances the nightly with.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qa.config import Config

DURATIONS = "scripts/shard_durations.json"
DEFAULT_MINUTES = 5.0


def minutes(root: Path) -> dict[str, float]:
    path = root / DURATIONS
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("minutes", {})


def stack_ids(cfg: Config, plan: dict[str, Any], rows: dict[str, dict[str, Any]]) -> list[str]:
    out = []
    for t in plan["tests"]:
        row = rows.get(t["id"])
        if row is None:
            continue
        suite = cfg.suite(row["suite"]) or {}
        if suite.get("stack") or row.get("family") == "playwright":
            out.append(t["id"])
    return out


def groups(rows: dict[str, dict[str, Any]], ids: list[str], mins: dict[str, float]) -> dict[str, tuple[float, list[str]]]:
    out: dict[str, tuple[float, list[str]]] = {}
    for i in ids:
        row = rows[i]
        key = next((m for m in row.get("markers") or [] if m in mins), row["path"])
        weight = mins.get(key, DEFAULT_MINUTES)
        out.setdefault(key, (weight, []))[1].append(i)
    return out


def assign(groups: dict[str, tuple[float, list[str]]], n: int) -> list[list[str]]:
    """Longest processing time first: each group goes to the lightest shard."""
    load = [0.0] * n
    shards: list[list[str]] = [[] for _ in range(n)]
    for key, (weight, ids) in sorted(groups.items(), key=lambda kv: (-kv[1][0], kv[0])):
        k = load.index(min(load))
        load[k] += weight
        shards[k] += ids
    return [sorted(s) for s in shards]


def _groups(cfg: Config, plan: dict[str, Any], rows: dict[str, dict[str, Any]]) -> dict[str, tuple[float, list[str]]]:
    return groups(rows, stack_ids(cfg, plan, rows), minutes(cfg.root))


def count(cfg: Config, plan: dict[str, Any], rows: dict[str, dict[str, Any]], cap: int) -> int:
    return max(1, min(cap, len(_groups(cfg, plan, rows))))


def select(cfg: Config, plan: dict[str, Any], rows: dict[str, dict[str, Any]], k: int, n: int) -> set[str]:
    """The ids of shard k (1-based) of n."""
    return set(assign(_groups(cfg, plan, rows), n)[k - 1])
