#!/usr/bin/env python3
"""Prepare a fresh PipesHub instance for the SDK test suites.

Creates the org and admin user, marks onboarding configured, mints one OAuth app
(client_credentials, all scopes) and adds the default LLM. The generated tests
read the four PIPESHUB_* variables this script writes to $GITHUB_ENV and, when
SDK_TEST_ENV_PATH is set, to that file.

Env vars:
    PIPESHUB_BASE_URL            default http://localhost:3000
    PIPESHUB_TEST_USER_EMAIL     required
    PIPESHUB_TEST_USER_PASSWORD  required
    TEST_AZURE_OPENAI_*          read by helper/ai_models_setup.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "helper"))

from ai_models_setup import list_configured_llm_models, setup_test_llm_model  # noqa: E402
from local_auth import obtain_local_oauth_credentials, obtain_user_session_token  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [provision-sdk] %(message)s")
log = logging.getLogger("provision-sdk")

_TIMEOUT = 30


def create_org(base_url: str, email: str, password: str) -> None:
    response = requests.post(
        f"{base_url}/api/v1/org",
        json={
            "accountType": "business",
            "adminFullName": "SDK Test Admin",
            "contactEmail": email,
            "password": password,
            "confirmPassword": password,
            "registeredName": "SDK Test Org",
            "shortName": "SDKTEST",
        },
        timeout=_TIMEOUT,
    )
    if response.ok:
        log.info("Created org and admin user")
        return
    # A rerun against the same instance: the login below fails if the org is not ours.
    log.info("Org not created (HTTP %s); assuming it already exists", response.status_code)


def mark_onboarding_configured(base_url: str) -> None:
    token = obtain_user_session_token(base_url, _TIMEOUT)
    response = requests.put(
        f"{base_url}/api/v1/org/onboarding-status",
        json={"status": "configured"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()


def seed_llm(client: PipeshubClient) -> None:
    if list_configured_llm_models(client):
        log.info("LLM already configured")
        return
    seeded = setup_test_llm_model(client)
    log.info("Seeded LLM: provider=%s model=%s", seeded.provider, seeded.model_name)


def export_env(values: dict[str, str]) -> None:
    print(f"::add-mask::{values['PIPESHUB_CLIENT_SECRET']}")
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    for target in (os.getenv("GITHUB_ENV"), os.getenv("SDK_TEST_ENV_PATH")):
        if target:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(lines)


def main() -> int:
    base_url = (os.getenv("PIPESHUB_BASE_URL") or "http://localhost:3000").rstrip("/")
    email = os.getenv("PIPESHUB_TEST_USER_EMAIL", "").strip()
    password = os.getenv("PIPESHUB_TEST_USER_PASSWORD", "").strip()
    if not email or not password:
        log.error("PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD are required")
        return 2

    create_org(base_url, email, password)
    mark_onboarding_configured(base_url)

    client_id, client_secret = obtain_local_oauth_credentials(base_url)
    os.environ["CLIENT_ID"] = client_id
    os.environ["CLIENT_SECRET"] = client_secret
    seed_llm(PipeshubClient(base_url=base_url))

    export_env(
        {
            "PIPESHUB_API_URL": f"{base_url}/api/v1",
            "PIPESHUB_TOKEN_URL": f"{base_url}/api/v1/oauth2/token",
            "PIPESHUB_CLIENT_ID": client_id,
            "PIPESHUB_CLIENT_SECRET": client_secret,
        }
    )
    log.info("Provisioning complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
