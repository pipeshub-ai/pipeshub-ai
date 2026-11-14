#!/usr/bin/env python3
"""Create the test org and admin, then mark onboarding done.

    PIPESHUB_TEST_USER_EMAIL=... PIPESHUB_TEST_USER_PASSWORD=... \
        python3 integration-tests/stack/seed.py --base-url http://localhost:3000

Idempotent: an org that already exists is reused, so a laptop stack can be
seeded again. This replaces the curl steps in integration-tests.yml so CI and
`qa run` seed the same way. Stdlib only.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


class SeedError(RuntimeError):
    pass


def _call(method: str, url: str, body: dict[str, Any] | None, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], dict[str, Any]]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as err:
        raw = err.read()
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw.decode(errors="replace")[:300]}
        return err.code, dict(err.headers), payload


def _claims(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


def seed(base_url: str, email: str, password: str, name: str = "Integration Test", short: str = "INTTEST") -> str:
    base = base_url.rstrip("/")
    status, _, payload = _call("POST", f"{base}/api/v1/org", {
        "accountType": "business",
        "adminFullName": f"{name} Admin",
        "contactEmail": email,
        "password": password,
        "confirmPassword": password,
        "registeredName": f"{name} Org",
        "shortName": short,
    })
    # 400/409: the org exists. 401/403: a multi-org stack only creates workspaces
    # for verified signups, so the admin in the env must already exist.
    if status >= 400 and status not in (400, 401, 403, 409):
        raise SeedError(f"creating the org failed with HTTP {status}: {payload}")
    created = status < 400
    refused = status in (401, 403)

    status, headers, _ = _call("POST", f"{base}/api/v1/userAccount/initAuth", {"email": email})
    session = next((v for k, v in headers.items() if k.lower() == "x-session-token"), None)
    if status >= 400 or not session:
        raise SeedError(f"initAuth failed with HTTP {status} (no session token)")

    status, _, payload = _call(
        "POST", f"{base}/api/v1/userAccount/authenticate",
        {"method": "password", "credentials": {"password": password}, "email": email},
        {"x-session-token": session},
    )
    token = payload.get("accessToken")
    if status < 400 and token and "userId" not in _claims(token):
        # Some editions answer with an email-verified token and the auto-login orgId; the org
        # token comes from token/switch, as in helper/local_auth.py.
        status, _, payload = _call(
            "POST", f"{base}/api/v1/auth/token/switch", {"orgId": payload.get("orgId")},
            {"Authorization": f"Bearer {token}"},
        )
        token = payload.get("accessToken")
    if status >= 400 or not token:
        if refused:
            raise SeedError(
                "this stack only creates workspaces for verified signups, and the admin in "
                "PIPESHUB_TEST_USER_EMAIL / PIPESHUB_TEST_USER_PASSWORD could not log in; use an existing admin"
            )
        raise SeedError(f"authenticate failed with HTTP {status}; is the org's admin password the one in the env?")

    status, _, payload = _call(
        "PUT", f"{base}/api/v1/org/onboarding-status", {"status": "configured"},
        {"Authorization": f"Bearer {token}"},
    )
    if status >= 400:
        raise SeedError(f"marking onboarding configured failed with HTTP {status}: {payload}")
    return "created" if created else "reused"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--base-url", default=os.environ.get("PIPESHUB_BASE_URL", "http://localhost:3000"))
    args = p.parse_args(argv)
    email = os.environ.get("PIPESHUB_TEST_USER_EMAIL")
    password = os.environ.get("PIPESHUB_TEST_USER_PASSWORD")
    if not email or not password:
        print("set PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD", file=sys.stderr)
        return 2
    try:
        print(f"org {seed(args.base_url, email, password)}; onboarding configured")
    except SeedError as err:
        print(str(err), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
