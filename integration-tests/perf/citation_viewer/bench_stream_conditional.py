"""Conditional-read benchmark for the record stream route.

Against a running stack: uploads one small text file to a throwaway knowledge base, reads it once for its
``ETag``, then times ``GET /api/v1/knowledgeBase/stream/record/<id>`` with ``If-None-Match`` (a 304: one
verified storage read, no bytes) at 1 and at 16 clients, and the plain 200 path. The 304 p95 must be
within ``NOT_MODIFIED_P95_BUDGET_MS``. A history row is appended, and a measure more than 10 % above the
median of recent runs fails the run. ``--base-200-p95-ms`` takes the 200-path p95 of the base build, so the
delta of the two bounded verified reads is reported.

Needs the integration-test environment (``PIPESHUB_BASE_URL`` and credentials).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_IT = _HERE.parent.parent
for _p in (_HERE, _HERE.parent, _IT / "helper", _IT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from viewer_stats import (  # noqa: E402
    append_history,
    over_budget,
    percentile,
    read_history,
    regressions,
)

STREAM = "/api/v1/knowledgeBase/stream/record"
HISTORY = _IT / "perf" / "history" / "citation-viewer.jsonl"
WAIT_FOR_RECORD_S = 180


def _time_reads(client, url: str, headers: dict[str, str], clients: int, per_client: int, expect: int) -> list[float]:
    def one(_: int) -> list[float]:
        out = []
        for _i in range(per_client):
            t0 = time.perf_counter()
            r = client.request("GET", url, headers=headers)
            out.append((time.perf_counter() - t0) * 1000)
            if r.status_code != expect:
                raise SystemExit(f"expected {expect}, got {r.status_code} from {url}")
        return out

    with ThreadPoolExecutor(max_workers=clients) as pool:
        return [v for chunk in pool.map(one, range(clients)) for v in chunk]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reads", type=int, default=60, help="reads per client")
    ap.add_argument("--base-200-p95-ms", type=float, default=None)
    ap.add_argument("--output", default="reports/perf/citation-viewer.json")
    ap.add_argument("--no-history", action="store_true")
    args = ap.parse_args()

    from stack import ensure_client_credentials, load_env  # noqa: E402

    load_env()
    import os

    from helper.clients.kb_client import KBClient
    from pipeshub_client import PipeshubClient

    base = os.environ["PIPESHUB_BASE_URL"].rstrip("/")
    ensure_client_credentials(base)
    client = PipeshubClient(base_url=base)
    kb = KBClient(client)
    kb_id = kb.create_kb(f"perf-citation-viewer-{uuid.uuid4().hex[:8]}")["id"]
    try:
        up = kb.upload_file(kb_id, "probe.txt", (f"viewer probe {uuid.uuid4()}\n" * 200).encode())
        record_id = (up.get("records") or [{}])[0].get("recordId") or (up.get("records") or [{}])[0].get("id")
        if not record_id:
            raise SystemExit(f"upload returned no record: {json.dumps(up)[:300]}")
        url = f"{STREAM}/{record_id}"
        deadline = time.monotonic() + WAIT_FOR_RECORD_S
        etag = None
        while time.monotonic() < deadline:
            r = client.request("GET", url)
            if r.status_code == 200 and r.headers.get("ETag"):
                etag = r.headers["ETag"]
                break
            time.sleep(3)
        if not etag:
            raise SystemExit("the stream route never answered 200 with an ETag: the validator is missing")

        result: dict = {
            "benchmark": "citation-viewer",
            "at": datetime.now(timezone.utc).isoformat(),
            "reads_per_client": args.reads,
        }
        p95: dict[int, float | None] = {}
        for clients in (1, 16):
            samples = _time_reads(client, url, {"If-None-Match": etag}, clients, args.reads, 304)
            p95[clients] = percentile(samples, 95)
            result[f"not_modified_p95_ms_{clients}"] = p95[clients]
            result[f"not_modified_p50_ms_{clients}"] = percentile(samples, 50)
        full = _time_reads(client, url, {}, 1, args.reads, 200)
        result["full_p95_ms"] = percentile(full, 95)
        if args.base_200_p95_ms is not None and result["full_p95_ms"] is not None:
            result["full_p95_delta_ms"] = result["full_p95_ms"] - args.base_200_p95_ms
    finally:
        kb.delete_kb(kb_id) if hasattr(kb, "delete_kb") else None

    problems = over_budget(p95)
    keys = ["not_modified_p95_ms_1", "not_modified_p95_ms_16", "full_p95_ms"]
    problems += regressions(read_history(HISTORY), result, keys)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True))
    if not args.no_history:
        append_history(HISTORY, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    for p in problems:
        print(f"FAIL {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
