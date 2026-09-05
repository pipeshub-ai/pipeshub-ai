#!/usr/bin/env python3
"""Seed a PipesHub instance with data, and later prove that data survived an upgrade.

Used by upgrade_smoke.sh. Two modes:

    upgrade_seed.py seed   BASE_URL OUT.json
    upgrade_seed.py verify BASE_URL OUT.json AFTER.json

Everything goes through the public API rather than straight into Mongo or the
graph. That matters for two reasons: the test then exercises the same write path
a real user does, and it does not encode a schema a migration is entitled to
change. A test that reads Mongo directly fails every time a collection is
renamed, which is noise, not signal.

What is seeded is deliberately small and boring — an organisation, its admin,
and a couple of knowledge bases. The point is not coverage of features; it is to
have identifiable rows in every store the upgrade touches, so that "the stack
came back up" cannot be mistaken for "the data is still there".

Only the standard library is used, so this runs on a bare CI runner.
"""

from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any

TIMEOUT = 30
ORG_EMAIL = "upgrade-smoke@pipeshub.test"
ORG_PASSWORD = "UpgradeSmoke1!"
ADMIN_NAME = "Upgrade Smoke"
KB_NAMES = ("upgrade-smoke-alpha", "upgrade-smoke-beta")

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


class ApiError(RuntimeError):
    def __init__(self, status: int, body: str, url: str) -> None:
        super().__init__(f"{status} from {url}: {body[:300]}")
        self.status = status
        self.body = body


def call(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    token: str | None = None,
    session_token: str | None = None,
) -> tuple[int, Any, dict[str, str]]:
    url = f"{base.rstrip('/')}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if session_token:
        req.add_header("x-session-token", session_token)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ctx) as resp:
            raw = resp.read().decode("utf-8", "replace")
            headers = {k.lower(): v for k, v in resp.headers.items()}
            try:
                return resp.status, json.loads(raw) if raw else None, headers
            except json.JSONDecodeError:
                return resp.status, raw, headers
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        raise ApiError(exc.code, raw, url) from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"cannot reach {url}: {exc.reason}") from None


def retry(
    fn,
    *,
    attempts: int = 12,
    delay: int = 5,
    what: str = "call",
    retry_status: tuple[int, ...] = (429,),
):
    """The API answers before every service behind it is ready; retry briefly.

    4xx responses are normally a real rejection and are raised immediately —
    retrying a bad password twelve times only wastes a minute. `retry_status`
    carries the exceptions: signing up writes the user to Mongo and the graph
    asynchronously, so a call made moments later can legitimately 404 with
    "User not found" and succeed shortly after.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except (ApiError, RuntimeError) as exc:
            last = exc
            if (
                isinstance(exc, ApiError)
                and 400 <= exc.status < 500
                and exc.status not in retry_status
            ):
                raise  # a real rejection, not a warm-up failure
            if i < attempts - 1:
                time.sleep(delay)
    raise RuntimeError(f"{what} failed after {attempts} attempts: {last}")


# ---------------------------------------------------------------------------


def ensure_org(base: str) -> None:
    """Create the organisation, unless this instance already has one."""
    try:
        _, existing, _ = call(base, "/api/v1/org/exists")
        if isinstance(existing, dict) and existing.get("exists"):
            print("  org already exists — reusing")
            return
    except ApiError:
        pass  # older builds may not expose /exists; fall through to create

    def _create():
        return call(
            base,
            "/api/v1/org",
            method="POST",
            body={
                "accountType": "individual",
                "contactEmail": ORG_EMAIL,
                "adminFullName": ADMIN_NAME,
                "password": ORG_PASSWORD,
                "sendEmail": False,
            },
        )

    try:
        status, _, _ = retry(_create, what="create org")
        print(f"  created org ({status})")
    except ApiError as exc:
        # A second run against the same instance is not a failure.
        if exc.status in (400, 409):
            print("  org already present")
            return
        raise


def login(base: str) -> str:
    """Return a bearer token for the seeded admin.

    Sign-in is two calls. initAuth opens a server-side session and returns its
    id in the `x-session-token` response header; authenticate will not accept
    credentials without that header echoed back, so the token has to be carried
    between the two calls.
    """

    def _init():
        return call(base, "/api/v1/userAccount/initAuth", method="POST", body={"email": ORG_EMAIL})

    _, _, headers = retry(_init, what="initAuth")
    session_token = headers.get("x-session-token")
    if not session_token:
        raise RuntimeError("initAuth did not return an x-session-token header")

    def _auth():
        return call(
            base,
            "/api/v1/userAccount/authenticate",
            method="POST",
            body={"method": "password", "credentials": {"password": ORG_PASSWORD}},
            session_token=session_token,
        )

    _, payload, _ = retry(_auth, what="authenticate")
    token = (payload or {}).get("accessToken") if isinstance(payload, dict) else None
    if not token:
        raise RuntimeError(f"no accessToken in authenticate response: {str(payload)[:200]}")
    print("  authenticated")
    return token


def list_kbs(base: str, token: str) -> list[dict[str, Any]]:
    _, payload, _ = call(base, "/api/v1/knowledgeBase/", token=token)
    if isinstance(payload, dict):
        for key in ("knowledgeBases", "data", "items", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
        inner = payload.get("data")
        if isinstance(inner, dict):
            for key in ("knowledgeBases", "items", "results"):
                if isinstance(inner.get(key), list):
                    return inner[key]
    return payload if isinstance(payload, list) else []


def kb_name(entry: dict[str, Any]) -> str:
    for key in ("kbName", "name", "knowledgeBaseName", "title"):
        val = entry.get(key)
        if isinstance(val, str):
            return val
    return ""


def await_user_ready(base: str, token: str) -> None:
    """Block until the freshly created admin is usable for writes.

    Signup returns before the user exists everywhere. Listing knowledge bases is
    the cheapest call that goes through the same lookup a write does, so when it
    stops returning "User not found" the account is ready.
    """

    def _probe():
        return call(base, "/api/v1/knowledgeBase/", token=token)

    retry(_probe, attempts=24, delay=5, what="wait for user provisioning",
          retry_status=(404, 429))
    print("  admin account provisioned")


def seed(base: str, out_path: str) -> int:
    print("seeding")
    ensure_org(base)
    token = login(base)
    await_user_ready(base, token)

    existing = {kb_name(k) for k in list_kbs(base, token)}
    created = []
    for name in KB_NAMES:
        if name in existing:
            print(f"  knowledge base already present: {name}")
            created.append(name)
            continue

        def _mk(n=name):
            return call(base, "/api/v1/knowledgeBase/", method="POST", body={"kbName": n}, token=token)

        retry(_mk, what=f"create kb {name}", retry_status=(404, 429))
        print(f"  created knowledge base: {name}")
        created.append(name)

    names = sorted({kb_name(k) for k in list_kbs(base, token)} & set(KB_NAMES))
    if sorted(KB_NAMES) != names:
        raise RuntimeError(f"seeded {KB_NAMES} but only {names} are readable back")

    fingerprint = {
        "org_email": ORG_EMAIL,
        "kb_names": names,
        "summary": f"1 org, 1 admin, {len(names)} knowledge bases",
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(fingerprint, fh, indent=2)
    print(f"  fingerprint written: {fingerprint['summary']}")
    return 0


def verify(base: str, before_path: str, after_path: str) -> int:
    print("verifying")
    with open(before_path, encoding="utf-8") as fh:
        before = json.load(fh)

    # Logging in at all proves the org, the admin and the password hash survived,
    # and that SECRET_KEY still decrypts what was stored before the upgrade.
    token = login(base)

    names = sorted({kb_name(k) for k in list_kbs(base, token)} & set(before["kb_names"]))
    after = {"org_email": before["org_email"], "kb_names": names}
    with open(after_path, "w", encoding="utf-8") as fh:
        json.dump(after, fh, indent=2)

    missing = sorted(set(before["kb_names"]) - set(names))
    if missing:
        print(f"  MISSING after upgrade: {missing}", file=sys.stderr)
        print(f"  before: {before['kb_names']}", file=sys.stderr)
        print(f"  after : {names}", file=sys.stderr)
        return 1

    print(f"  admin can still authenticate: {before['org_email']}")
    print(f"  knowledge bases intact: {names}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    mode = argv[1]
    try:
        if mode == "seed" and len(argv) == 4:
            return seed(argv[2], argv[3])
        if mode == "verify" and len(argv) == 5:
            return verify(argv[2], argv[3], argv[4])
    except Exception as exc:  # noqa: BLE001 — the message is the whole point here
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
