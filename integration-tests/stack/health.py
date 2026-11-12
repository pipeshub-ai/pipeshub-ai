#!/usr/bin/env python3
"""Wait until the platform answers its health endpoints.

    python3 integration-tests/stack/health.py --base-url http://localhost:3000 --timeout 300

Stdlib only, so the CI workflow and `qa run` call the same code.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

ENDPOINTS = ("/api/v1/health", "/api/v1/health/services")


def ok(url: str) -> bool:
    # The API answers 200 while its services are still starting; only the body says healthy (as CI checks).
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = json.loads(resp.read() or b"{}")
            return 200 <= resp.status < 300 and isinstance(body, dict) and body.get("status") == "healthy"
    except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError):
        return False


def wait(base_url: str, timeout: float, interval: float = 5.0) -> list[str]:
    """Endpoints still unhealthy when the timeout ran out (empty = healthy)."""
    deadline = time.monotonic() + timeout
    pending = list(ENDPOINTS)
    while pending and time.monotonic() < deadline:
        pending = [e for e in pending if not ok(base_url.rstrip("/") + e)]
        if pending:
            time.sleep(interval)
    return pending


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", required=True)
    p.add_argument("--timeout", type=float, default=300)
    args = p.parse_args(argv)
    pending = wait(args.base_url, args.timeout)
    if pending:
        print(f"unhealthy after {args.timeout:.0f}s: {', '.join(pending)}", file=sys.stderr)
        return 1
    print("all services healthy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
