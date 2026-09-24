#!/usr/bin/env python3
"""Aggregate `PHASE qid=... step=... ms=... cum=...` lines into a per-phase table.

    sudo docker logs --since <ts> pipeshub-ai 2>&1 | agg_phases.py

Only requests that reached a terminal step are counted, so a trial that is cut
off mid-flight cannot inflate a phase's share by dropping the phases that would
have followed it.
"""

import re
import sys
from collections import defaultdict

LINE = re.compile(
    r"PHASE qid=(?P<qid>\w+) step=(?P<step>\w+) ms=(?P<ms>[\d.]+) cum=(?P<cum>[\d.]+)"
)
TERMINAL = ("finalize", "llm_stream")
ORDER = [
    "llm_config", "user_context", "connector_probe", "attachments", "chat_state",
    "connector_prefetch", "agent_create", "retrieval_wait", "llm_stream", "finalize",
]


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))]


def main() -> int:
    per_q: dict = defaultdict(dict)
    cum_of: dict = {}
    for raw in sys.stdin:
        m = LINE.search(raw)
        if not m:
            continue
        qid, step = m["qid"], m["step"]
        per_q[qid][step] = float(m["ms"])
        cum_of.setdefault(qid, {})[step] = float(m["cum"])

    complete = [q for q, steps in per_q.items() if any(t in steps for t in TERMINAL)]
    if not complete:
        print("no complete requests found", file=sys.stderr)
        return 1

    totals = [max(cum_of[q].values()) for q in complete]
    grand = sum(totals)

    print(f"requests: {len(complete)} complete ({len(per_q)} seen)")
    print(f"turn total  mean={sum(totals)/len(totals):8.0f}ms  "
          f"p50={pct(totals,50):8.0f}ms  p95={pct(totals,95):8.0f}ms")
    print()
    print(f"{'phase':<20}{'n':>5}{'mean ms':>10}{'p50':>9}{'p95':>9}{'max':>9}{'% turn':>9}")
    print("-" * 71)

    seen = [s for s in ORDER if any(s in per_q[q] for q in complete)]
    seen += sorted({s for q in complete for s in per_q[q]} - set(seen))
    for step in seen:
        vals = [per_q[q][step] for q in complete if step in per_q[q]]
        if not vals:
            continue
        share = 100.0 * sum(vals) / grand if grand else 0.0
        print(f"{step:<20}{len(vals):>5}{sum(vals)/len(vals):>10.0f}"
              f"{pct(vals,50):>9.0f}{pct(vals,95):>9.0f}{max(vals):>9.0f}{share:>8.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
