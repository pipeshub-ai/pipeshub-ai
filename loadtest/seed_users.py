#!/usr/bin/env python3
"""Provision load-test users on a local stack.

The accessible-record cache is keyed per user, so a run driven by one identity
measures a cache hit rate no real deployment would see. This creates N users
that all share the org's knowledge bases, so the multi-user run exercises the
real thing.

Creating a user needs an org admin token. The *invite* route needs SMTP, but
`POST /api/v1/users` does not — it creates the account with no credentials and
sends no mail — so no mail server has to exist for any of this.

Giving that account a password, in order of preference:

1. `POST /api/v1/userAccount/password/reset/token` with a `password:reset`
   scoped JWT. Pure API, nothing touches the database. Needs the deployment's
   scoped JWT secret in `PIPESHUB_SCOPED_JWT_SECRET` (see --print-secret-help).
2. Writing bcrypt credentials into Mongo directly, the way
   `integration-tests/helper/second_user_auth.py` does. Needs `bcrypt` and
   `pymongo`, and a reachable Mongo.

    export PIPESHUB_ADMIN_EMAIL=admin@example.com
    export PIPESHUB_ADMIN_PASSWORD='...'
    export PIPESHUB_SCOPED_JWT_SECRET='...'
    ./seed_users.py --count 8

Writes the `PIPESHUB_USERS` line to a 0600 file to paste into `.env` --
not to stdout, which reaches scrollback, CI logs and shell history.
Local stacks only.
"""

from __future__ import annotations

import argparse
import datetime
import os
import subprocess
from pathlib import Path
import sys
import uuid

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeshub_auth import AuthError, login  # noqa: E402

DEFAULT_BASE_URL = os.environ.get("PIPESHUB_HOST", "http://localhost:3000")
DEFAULT_PASSWORD = os.environ.get("PIPESHUB_SEED_PASSWORD", "LoadTest123!")
DEFAULT_EMAIL_DOMAIN = "loadtest.pipeshub.local"
MONGO_URI = os.environ.get("PIPESHUB_MONGO_URI", "mongodb://admin:password@localhost:27017/?authSource=admin")
MONGO_DB = os.environ.get("PIPESHUB_MONGO_DB", "es")
MONGO_CONTAINER = os.environ.get("PIPESHUB_MONGO_CONTAINER", "mongodb")
PASSWORD_RESET_SCOPE = "password:reset"
TIMEOUT = 30


def create_user(base_url: str, admin_token: str, email: str, full_name: str) -> dict:
    resp = requests.post(
        f"{base_url.rstrip('/')}/api/v1/users",
        headers={"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"},
        json={"fullName": full_name, "email": email},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"createUser failed for {email}: HTTP {resp.status_code}: {resp.text[:300]}"
        )
    try:
        data = resp.json()
    except ValueError as e:
        raise RuntimeError(f"createUser returned non-JSON for {email}") from e
    if not isinstance(data, dict):
        raise RuntimeError(
            f"createUser returned non-object JSON for {email}: {type(data).__name__}"
        )
    user_id = data.get("_id") or data.get("id")
    org_id = data.get("orgId")
    if not isinstance(user_id, str) or not user_id or not isinstance(org_id, str) or not org_id:
        raise RuntimeError(
            f"createUser response missing string _id/id and orgId for {email}: "
            f"{sorted(data)}"
        )
    return {"_id": user_id, "id": user_id, "orgId": org_id}


def _credential_document(user_id: str, org_id: str, hashed: str) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        # Both ids are declared as String in userCredentials.schema.ts.
        "userId": str(user_id),
        "orgId": str(org_id),
        "hashedPassword": hashed,
        "ipAddress": "127.0.0.1",
        "wrongCredentialCount": 0,
        "isBlocked": False,
        "forceNewPasswordGeneration": False,
        "isDeleted": False,
        "createdAt": now,
        "updatedAt": now,
    }


def seed_password_direct(user_id: str, org_id: str, password: str) -> None:
    """Write credentials through a direct Mongo connection."""
    import bcrypt
    from pymongo import MongoClient

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    try:
        collection = client[MONGO_DB].userCredentials
        collection.delete_many({"userId": str(user_id), "orgId": str(org_id)})
        collection.insert_one(_credential_document(user_id, org_id, hashed))
    finally:
        client.close()


def seed_password_via_docker(user_id: str, org_id: str, password: str) -> None:
    """Fallback for the standard compose file, which does not publish 27017."""
    import bcrypt

    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    script = f"""
    db = db.getSiblingDB("{MONGO_DB}");
    db.userCredentials.deleteMany({{userId: "{user_id}", orgId: "{org_id}"}});
    db.userCredentials.insertOne({{
        userId: "{user_id}",
        orgId: "{org_id}",
        hashedPassword: "{hashed}",
        ipAddress: "127.0.0.1",
        wrongCredentialCount: 0,
        isBlocked: false,
        forceNewPasswordGeneration: false,
        isDeleted: false,
        createdAt: new Date(),
        updatedAt: new Date()
    }});
    """
    result = subprocess.run(
        ["docker", "exec", "-i", MONGO_CONTAINER, "mongosh", "--quiet",
         MONGO_URI.replace("/?", f"/{MONGO_DB}?"), "--eval", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"mongosh via docker exec failed: {result.stderr[:400] or result.stdout[:400]}"
        )


def seed_password_via_api(base_url: str, user_id: str, org_id: str, password: str) -> None:
    """Set the password through the reset-token route — no database access.

    The route accepts any token signed with the deployment's scoped secret that
    carries the `password:reset` scope, which is the same thing the emailed
    reset link contains.
    """
    import jwt

    secret = os.environ.get("PIPESHUB_SCOPED_JWT_SECRET", "").strip()
    if not secret:
        raise RuntimeError("PIPESHUB_SCOPED_JWT_SECRET is not set")

    scoped_token = jwt.encode(
        {"userId": str(user_id), "orgId": str(org_id), "scopes": [PASSWORD_RESET_SCOPE]},
        secret,
        algorithm="HS256",
    )
    resp = requests.post(
        f"{base_url.rstrip('/')}/api/v1/userAccount/password/reset/token",
        headers={"Authorization": f"Bearer {scoped_token}", "Content-Type": "application/json"},
        json={"password": password},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"password reset failed: HTTP {resp.status_code}: {resp.text[:200]}")


def seed_password(base_url: str, user_id: str, org_id: str, password: str) -> str:
    errors: list[str] = []
    for label, attempt in (
        ("api", lambda: seed_password_via_api(base_url, user_id, org_id, password)),
        ("mongo", lambda: seed_password_direct(user_id, org_id, password)),
        ("docker", lambda: seed_password_via_docker(user_id, org_id, password)),
    ):
        try:
            attempt()
            return label
        except Exception as e:  # noqa: BLE001 - try the next strategy
            errors.append(f"{label}: {e}")
    raise RuntimeError("could not set a password.\n  " + "\n  ".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=8, help="users to provision (default 8)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--password", default=DEFAULT_PASSWORD,
                        help="needs 8+ chars with upper, lower, digit and symbol")
    parser.add_argument("--prefix", default="loadtest", help="email local-part prefix")
    parser.add_argument("--domain", default=DEFAULT_EMAIL_DOMAIN)
    parser.add_argument("--credentials-out", default="loadtest_users.env",
                        help="file the PIPESHUB_USERS line is written to (mode 0600)")
    args = parser.parse_args()

    admin_email = os.environ.get("PIPESHUB_ADMIN_EMAIL", "").strip()
    admin_password = os.environ.get("PIPESHUB_ADMIN_PASSWORD", "").strip()
    if not admin_email or not admin_password:
        print("ERROR: set PIPESHUB_ADMIN_EMAIL and PIPESHUB_ADMIN_PASSWORD", file=sys.stderr)
        return 2

    print(f"Logging in as {admin_email} ...")
    try:
        admin_token = login(args.base_url, admin_email, admin_password)
    except AuthError as e:
        print(f"ERROR: admin login failed: {e}", file=sys.stderr)
        return 1

    run_id = uuid.uuid4().hex[:6]
    created: list[tuple[str, str]] = []
    for i in range(args.count):
        email = f"{args.prefix}-{run_id}-{i}@{args.domain}"
        try:
            user = create_user(args.base_url, admin_token, email, f"Load Test {run_id} {i}")
            user_id = user["_id"]
            org_id = user["orgId"]
            how = seed_password(args.base_url, user_id, org_id, args.password)
            login(args.base_url, email, args.password)  # prove it works now, not mid-run
            created.append((email, args.password))
            print(f"  [{i + 1}/{args.count}] {email} (credentials via {how})")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR provisioning {email}: {e}", file=sys.stderr)
            return 1

    # Written to a 0600 file rather than stdout: these are real (if disposable)
    # credentials, and stdout ends up in terminal scrollback, CI logs and shell
    # history. The caller gets a path to paste from instead.
    line = "PIPESHUB_USERS=" + ",".join(f"{email}:{password}" for email, password in created)
    out_path = Path(args.credentials_out).expanduser()
    fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(line + "\n")

    print(f"\nProvisioned {len(created)} users.")
    print(f"Credentials written to {out_path} (mode 0600).")
    print(f"Add them to loadtest/.env:\n  cat {out_path} >> .env")
    print(
        "\nNote: these users see only what the org shares with them. Give them KB "
        "access if the run should search the same corpus."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
