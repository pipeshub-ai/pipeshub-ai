#!/usr/bin/env python3
"""Per-turn tool activity from the query service log, on stdin.

    log_read ... | agg_tools.py [--json out.json]

Lines carry the request id as `[req:<id>-qs ...]`, so markers the service
already logs can be counted per turn without a probe: how much retrieval work
a turn did. Only requests that reached `step=finalize` count.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict

REQ = re.compile(r"\[req:([A-Za-z0-9-]+?)-qs ")
MARKERS = {
    "knowledge search": "knowledgegraph__search: query_chars",
    "grep cmd via LLM": "generate_grep_command_via_llm: generated",
    "grep connector scans": "pattern_match _search_connector:",
    "fetch_full_record": "fetch_full_record called",
    "retrieval calls": "Unique record IDs count",
    "prefetch": "prefetch: needs_whole_document",
}


def pct(vals: list[float], q: float) -> float:
    s = sorted(vals)
    return s[min(int(q * (len(s) - 1) + 0.5), len(s) - 1)] if s else 0.0


def main(argv: list[str]) -> int:
    json_out = argv[argv.index("--json") + 1] if "--json" in argv else None
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    done: set[str] = set()
    for line in sys.stdin:
        m = REQ.search(line)
        if not m:
            continue
        rid = m.group(1)
        if "step=finalize" in line:
            done.add(rid)
            continue
        for name, marker in MARKERS.items():
            if marker in line:
                counts[rid][name] += 1
    turns = [counts[r] for r in done]
    if not turns:
        print("   (no finalized turns with request ids in this window)")
        return 0
    print(f"  {len(turns)} finalized turns")
    print("  per turn                 mean    p50    p95    max")
    out = {"turns": len(turns)}
    for name in MARKERS:
        vals = [t.get(name, 0) for t in turns]
        print(f"  {name:<22}{sum(vals) / len(vals):>7.1f}{pct(vals, .5):>7.0f}{pct(vals, .95):>7.0f}{max(vals):>7.0f}")
        out[name] = {"mean": round(sum(vals) / len(vals), 2), "p95": pct(vals, .95)}
    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
