#!/usr/bin/env python3
"""Print one access token per configured load-test user, newline separated.

Called by `perftest.sh` so the shell harness gets the same identities the
locust harness does. Exits non-zero on the first failure — a run with fewer
users than intended measures the wrong thing.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.environ.get("LOADTEST_DIR") or os.path.dirname(os.path.abspath(__file__)))

from pipeshub_auth import AuthError, credentials_from_env, resolve_tokens  # noqa: E402


def main() -> int:
    host = os.environ.get("PIPESHUB_HOST", "http://localhost:3000")
    try:
        credentials = credentials_from_env()
        if not credentials:
            raise AuthError("PIPESHUB_USERS/PIPESHUB_EMAILS is set but parsed to no credentials")
        for token in resolve_tokens(host, credentials):
            print(token)
    except AuthError as e:
        print(f"AUTH ERROR: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
