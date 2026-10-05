"""One-shot setup of a fresh stack for load testing.

Creates the org + admin account, mints an OAuth2 client-credentials app, and
writes the result into loadtest/.env so `run` works immediately afterwards.

The OAuth app creation is not reimplemented here: integration-tests already has
`helper/local_auth.py` doing the initAuth -> authenticate -> create-client dance
with the full scope list. Only org creation is new, because the integration
tests assume an org already exists.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import requests

from .client import _HELPER_DIR

DEFAULTS = {
    "email": "loadtest@pipeshub.local",
    "password": "LoadTest123!",
    "org_name": "Loadtest Org",
    "admin_name": "Load Test",
}


class BootstrapError(RuntimeError):
    pass


def _post(base_url: str, path: str, payload: dict, timeout: int = 60) -> requests.Response:
    return requests.post(f"{base_url.rstrip('/')}{path}", json=payload, timeout=timeout)


def create_org(base_url: str, email: str, password: str, org_name: str, admin_name: str) -> dict[str, Any]:
    """POST /api/v1/org/ — the unauthenticated first-run signup.

    Idempotent in practice: a second call against an initialised stack is
    rejected, which is fine — the caller then just logs in.
    """
    resp = _post(
        base_url,
        "/api/v1/org/",
        {
            "accountType": "business",
            "registeredName": org_name,
            "shortName": org_name.lower().replace(" ", "-"),
            "contactEmail": email,
            "adminFullName": admin_name,
            "password": password,
            # No SMTP is configured on a load-test box; asking for mail would
            # only produce a failure the operator has to interpret.
            "sendEmail": False,
        },
    )
    if resp.status_code < 400:
        return {"created": True, "status": resp.status_code}
    body = resp.text[:300]
    # An already-initialised stack is not an error for this command.
    if resp.status_code in (400, 409) and (
        "already" in body.lower() or "exist" in body.lower()
    ):
        return {"created": False, "status": resp.status_code, "detail": body}
    raise BootstrapError(f"org creation failed: HTTP {resp.status_code} — {body}")


def mint_oauth_client(base_url: str, email: str, password: str) -> tuple[str, str]:
    if str(_HELPER_DIR) not in sys.path:
        sys.path.insert(0, str(_HELPER_DIR))
    # Imported late: the module only resolves once integration-tests/helper is
    # on sys.path, which the line above arranges.
    from local_auth import (  # type: ignore[import-not-found]  # noqa: PLC0415
        obtain_local_oauth_credentials,
    )

    os.environ["PIPESHUB_TEST_USER_EMAIL"] = email
    os.environ["PIPESHUB_TEST_USER_PASSWORD"] = password
    return obtain_local_oauth_credentials(base_url)


def write_env(env_path: Path, values: dict[str, str]) -> None:
    """Merge `values` into the .env, preserving anything already there."""
    existing: dict[str, str] = {}
    order: list[str] = []
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            if key not in existing:
                order.append(key)
            existing[key] = value.strip()

    for key, value in values.items():
        if key not in existing:
            order.append(key)
        existing[key] = value

    env_path.write_text(
        "\n".join(f"{key}={existing[key]}" for key in order) + "\n", encoding="utf-8"
    )


def run(base_url: str, env_path: Path, **overrides: str) -> dict[str, Any]:
    settings = {**DEFAULTS, **{k: v for k, v in overrides.items() if v}}
    org = create_org(
        base_url,
        settings["email"],
        settings["password"],
        settings["org_name"],
        settings["admin_name"],
    )
    client_id, client_secret = mint_oauth_client(
        base_url, settings["email"], settings["password"]
    )
    write_env(
        env_path,
        {
            "PIPESHUB_BASE_URL": base_url,
            "CLIENT_ID": client_id,
            "CLIENT_SECRET": client_secret,
        },
    )
    return {
        "org_created": org["created"],
        "email": settings["email"],
        "client_id": client_id,
        "env_path": str(env_path),
    }
