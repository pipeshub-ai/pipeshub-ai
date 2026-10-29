"""Golden diff: parsed blocks for the corpus, to prove a change leaves output unchanged.

Each parsed file is reduced to a canonical form (block and group ids renamed by
order of first appearance, keys sorted) and hashed. ``write`` records the
hashes on the reference checkout (main); ``check`` compares another checkout's
run against them and names every file that differs.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def canonicalize(payload: Any) -> str:  # noqa: ANN401
    """Stable JSON of a dumped container: uuids renamed by first appearance."""
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    names: dict[str, str] = {}

    def rename(match: re.Match[str]) -> str:
        return names.setdefault(match.group(0).lower(), f"id{len(names)}")

    return _UUID.sub(rename, text)


def digest(payload: Any) -> str:  # noqa: ANN401
    return hashlib.sha256(canonicalize(payload).encode()).hexdigest()


def summarize(payload: Any, blocks: int, groups: int) -> dict[str, Any]:  # noqa: ANN401
    """Hash plus counts, so a mismatch says how big the difference is."""
    return {"sha256": digest(payload), "blocks": blocks, "groups": groups}


def compare(
    golden: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
    *,
    subset: bool = False,
) -> list[str]:
    """Human-readable differences; empty means identical.

    With *subset*, a file the golden file has and this run lacks is not a difference
    (a run of one mix checks only the files it parsed).
    """
    problems = []
    for name in sorted(set(golden) | set(current)):
        if name not in current:
            if not subset:
                problems.append(f"{name}: missing from this run")
        elif name not in golden:
            problems.append(f"{name}: not in the golden file")
        elif golden[name].get("error") or current[name].get("error"):
            if golden[name].get("error") != current[name].get("error"):
                problems.append(f"{name}: error {golden[name].get('error')!r} -> {current[name].get('error')!r}")
        elif golden[name]["sha256"] != current[name]["sha256"]:
            g, c = golden[name], current[name]
            problems.append(
                f"{name}: blocks {g['blocks']} -> {c['blocks']}, groups {g['groups']} -> {c['groups']}, hash differs"
            )
    return problems
