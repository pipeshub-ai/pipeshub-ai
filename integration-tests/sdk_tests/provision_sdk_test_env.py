#!/usr/bin/env python3
"""Prepare a fresh PipesHub instance for the SDK test suites.

Creates the org and admin user, marks onboarding configured, mints one OAuth app
(client_credentials, all scopes), adds the default LLM and embedding model, and
indexes one document so search has something to answer from. The tests read the
four PIPESHUB_* variables this script writes to $GITHUB_ENV and, when
SDK_TEST_ENV_PATH is set, to that file.

Env vars:
    PIPESHUB_BASE_URL            default http://localhost:3000
    PIPESHUB_TEST_USER_EMAIL     required
    PIPESHUB_TEST_USER_PASSWORD  required
    TEST_AZURE_OPENAI_*          read by helper/ai_models_setup.py
    SDK_SEED_INDEX_TIMEOUT       default 420 (seconds)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

import requests

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "helper"))

from ai_models_setup import (  # noqa: E402
    list_configured_llm_models,
    setup_test_embedding_model,
    setup_test_llm_model,
)
from local_auth import obtain_local_oauth_credentials, obtain_user_session_token  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s [provision-sdk] %(message)s"
)
log = logging.getLogger("provision-sdk")

_TIMEOUT = 30
_SEED_FILE_NAME = "sdk-test-seed.txt"
_SEED_FILE_CONTENT = b"PipesHub SDK test seed document. The codeword is pineapple.\n"
_SEED_QUERY = "What is the codeword?"


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
    log.info(
        "Org not created (HTTP %s); assuming it already exists", response.status_code
    )


def mark_onboarding_configured(base_url: str) -> None:
    token = obtain_user_session_token(base_url, _TIMEOUT)
    response = requests.put(
        f"{base_url}/api/v1/org/onboarding-status",
        json={"status": "configured"},
        headers={"Authorization": f"Bearer {token}"},
        timeout=_TIMEOUT,
    )
    response.raise_for_status()


def seed_models(client: PipeshubClient) -> None:
    if list_configured_llm_models(client):
        log.info("AI models already configured")
        return
    llm = setup_test_llm_model(client)
    embedding = setup_test_embedding_model(client)
    log.info(
        "Seeded LLM %s and embedding model %s", llm.model_name, embedding.model_name
    )


def client_credentials_token(base_url: str, client_id: str, client_secret: str) -> str:
    response = requests.post(
        f"{base_url}/api/v1/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def search_answers(base_url: str, headers: dict[str, str]) -> bool:
    """True once search has content. It answers 404 while the org has no indexed document."""
    response = requests.post(
        f"{base_url}/api/v1/search",
        json={"query": _SEED_QUERY},
        headers=headers,
        timeout=60,
    )
    if response.status_code == requests.codes.not_found:
        return False
    response.raise_for_status()
    return True


def seed_search_document(base_url: str, token: str) -> None:
    """Index one document: search answers 404 while the org has none."""
    headers = {"Authorization": f"Bearer {token}"}
    if search_answers(base_url, headers):
        log.info("Search already has indexed content")
        return

    created = requests.post(
        f"{base_url}/api/v1/knowledgeBase",
        json={"kbName": "sdk-test-seed"},
        headers=headers,
        timeout=_TIMEOUT,
    )
    created.raise_for_status()
    kb_id = created.json()["id"]

    upload = requests.post(
        f"{base_url}/api/v1/knowledgeBase/{kb_id}/upload",
        files={"files": (_SEED_FILE_NAME, _SEED_FILE_CONTENT, "text/plain")},
        headers=headers,
        timeout=120,
    )
    upload.raise_for_status()
    if "event: file:succeeded" not in upload.text:
        raise RuntimeError(f"Seed upload did not succeed: {upload.text[:500]}")

    deadline = time.monotonic() + int(os.getenv("SDK_SEED_INDEX_TIMEOUT", "420"))
    while not search_answers(base_url, headers):
        if time.monotonic() > deadline:
            raise RuntimeError(
                "Seed document was not indexed in time; search still has no content"
            )
        time.sleep(5)
    log.info("Seed document indexed (knowledge base %s)", kb_id)


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
        log.error(
            "PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD are required"
        )
        return 2

    create_org(base_url, email, password)
    mark_onboarding_configured(base_url)

    client_id, client_secret = obtain_local_oauth_credentials(base_url)
    os.environ["CLIENT_ID"] = client_id
    os.environ["CLIENT_SECRET"] = client_secret
    seed_models(PipeshubClient(base_url=base_url))
    seed_search_document(
        base_url, client_credentials_token(base_url, client_id, client_secret)
    )

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
