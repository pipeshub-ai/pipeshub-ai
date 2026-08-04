#!/usr/bin/env python3
"""Client-side request outcomes for one run, from `requests.csv`.

Exists because the load generator used to discard every response (`-o /dev/null`
and `|| true`): a run where every request returned 500 was indistinguishable
from one that was merely slow, and both showed up only as "low throughput".

An error rate above `INVALID_ABOVE` marks the run INVALID — a run that mostly
failed is not a measurement, and should not be averaged into a matrix.
"""
from __future__ import annotations

import csv
import math
import statistics as st
import sys

INVALID_ABOVE = 0.05

# curl's own exit codes, for failures that never produced an HTTP status.
CURL_ERRORS = {28: "timeout", 7: "connection refused", 52: "empty reply", 56: "recv error"}


def main() -> int:
    try:
        with open(sys.argv[1], newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, IndexError):
        print("  (no requests.csv)")
        return 0
    if not rows:
        print("  (no requests recorded)")
        return 0

    total = len(rows)
    ok, by_code, by_curl, times = 0, {}, {}, []
    for r in rows:
        code, rc = r.get("http_code", "000"), int(r.get("curl_exit") or 0)
        if rc != 0:
            by_curl[rc] = by_curl.get(rc, 0) + 1
        elif code == "200":
            ok += 1
            try:
                times.append(float(r["time_total"]))
            except (ValueError, KeyError):
                pass
        else:
            by_code[code] = by_code.get(code, 0) + 1

    failed = total - ok
    rate = failed / total
    print(f"  requests        : {total}")
    print(f"  succeeded (200) : {ok}")
    print(f"  failed          : {failed}  ({rate * 100:.1f}%)")
    for code, n in sorted(by_code.items()):
        print(f"     HTTP {code}     : {n}")
    for rc, n in sorted(by_curl.items()):
        print(f"     curl {rc:<3}     : {n}  ({CURL_ERRORS.get(rc, 'see curl(1)')})")
    if times:
        times.sort()
        # Client-side, so this includes queueing the server never sees.
        # Nearest-rank: ceil(0.95 * n) - 1. Truncating instead returned the
        # maximum whenever n was small enough for int() to round up to n-1.
        p95 = times[max(math.ceil(0.95 * len(times)) - 1, 0)]
        print(
            f"  turn seconds    : p50={st.median(times):.1f} "
            f"p95={p95:.1f} max={times[-1]:.1f}"
        )
    if rate > INVALID_ABOVE:
        print(f"  ** INVALID: {rate * 100:.1f}% of requests failed (>{INVALID_ABOVE * 100:.0f}%)")
        print("     this run measures the error path, not throughput.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
