#!/usr/bin/env python3
"""Provision a PipesHub instance for the SDK integration test suites.

The generated SDKs (``pipeshub-sdk-go``, ``pipeshub-sdk-python``,
``pipeshub-sdk-typescript``) each ship a real test suite on their
``auto/sdk-update`` branch. All three authenticate the **same way** — a single
OAuth app using the ``client_credentials`` grant — and read the **same four env
vars** (falling back to localhost when unset):

    PIPESHUB_API_URL        <base_url>/api/v1
    PIPESHUB_CLIENT_ID      OAuth app clientId
    PIPESHUB_CLIENT_SECRET  OAuth app clientSecret
    PIPESHUB_TOKEN_URL      <base_url>/api/v1/oauth2/token

The SDKs perform the token exchange themselves (Speakeasy ``oauth2`` security
scheme), so this script only mints the OAuth app and emits its client id/secret
— it never fetches a bearer token.

Against a freshly-bootstrapped instance (org + admin user created, onboarding
skipped — as the CI workflow does) this script:

1. mints one OAuth app with **all scopes** + ``client_credentials`` grant,
2. adds an LLM model (the indexing pipeline needs one to reach COMPLETED),
3. creates a KB and uploads a few sample files, waiting for indexing,

then exports the four env vars (plus ``KB_ID``) to ``$GITHUB_ENV`` (so later CI
steps inherit them), to stdout (with the secret masked), and optionally to a
``.env`` file for local runs.

It deliberately **reuses** the existing integration-test helpers:
- ``local_auth.obtain_local_oauth_credentials``  — all-scopes OAuth app
- ``ai_models_setup.setup_test_llm_model`` / ``list_configured_llm_models``
- ``PipeshubClient``                             — KB create + upload + wait
- ``sample_data.ensure_sample_data_files_root``  — KB content

Env vars:
    PIPESHUB_BASE_URL            default http://localhost:3000
    PIPESHUB_TEST_USER_EMAIL     required (admin login to mint the OAuth app)
    PIPESHUB_TEST_USER_PASSWORD  required
    TEST_OPENAI_API_KEY          required for the LLM; falls back to OPENAI_API_KEY
    SDK_KB_NAME                  default "SDK-test"
    SDK_KB_INDEX_TIMEOUT         default 420 (seconds)
    SDK_TEST_ENV_PATH            optional; also write a .env file here (local runs)
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Make the integration-tests helpers importable regardless of CWD.
_THIS_DIR = Path(__file__).resolve().parent
_ROOT = _THIS_DIR.parent  # integration-tests/
for p in (_ROOT, _ROOT / "helper", _ROOT / "sample-data"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from ai_models_setup import (  # noqa: E402
    list_configured_llm_models,
    setup_test_llm_model,
)
from local_auth import obtain_local_oauth_credentials  # noqa: E402
from pipeshub_client import PipeshubClient  # noqa: E402
from sample_data import ensure_sample_data_files_root  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [provision-sdk] %(message)s",
)
log = logging.getLogger("provision-sdk")

KB_NAME = os.getenv("SDK_KB_NAME", "SDK-test")


def _api_key() -> Optional[str]:
    return os.getenv("TEST_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")


def mint_oauth_app(base_url: str) -> tuple[str, str]:
    """Mint a client_credentials OAuth app with all scopes; return (id, secret).

    Always mints a fresh app against ``base_url`` — each graph-DB backend (neo4j /
    arango) runs on its own instance, so an app minted against one would not exist
    on the other. Sets CLIENT_ID/CLIENT_SECRET in the environment so the
    PipeshubClient built afterwards authenticates as this app for the admin calls
    below.
    """
    log.info("Minting OAuth app (client_credentials, all scopes) via %s", base_url)
    client_id, client_secret = obtain_local_oauth_credentials(base_url)
    os.environ["CLIENT_ID"] = client_id
    os.environ["CLIENT_SECRET"] = client_secret
    log.info("Minted OAuth app: client_id=%s", client_id)
    return client_id, client_secret


def seed_llm(client: PipeshubClient) -> None:
    existing = list_configured_llm_models(client)
    if existing:
        log.info("LLM already configured (%d model(s)); skipping", len(existing))
        return
    seeded = setup_test_llm_model(client)
    log.info("Seeded LLM: provider=%s model=%s", seeded.provider, seeded.model_name)


def seed_kb(client: PipeshubClient) -> str:
    kb_id = client.find_knowledge_base_id(KB_NAME)
    if kb_id:
        log.info("KB %r already exists (id=%s)", KB_NAME, kb_id)
    else:
        kb_id = client.create_knowledge_base(KB_NAME)
        log.info("Created KB %r (id=%s)", KB_NAME, kb_id)

    # Already has indexed content? Don't re-upload on reruns.
    already = client.list_records(kb_id=kb_id, indexing_status="COMPLETED")
    if already:
        log.info("KB %r already has %d COMPLETED record(s)", KB_NAME, len(already))
        return kb_id

    files = _pick_sample_files(limit=3)
    log.info("Uploading %d file(s) into KB %r: %s", len(files), KB_NAME, [f.name for f in files])
    records = client.upload_files_to_kb(kb_id, files)
    log.info("Upload accepted %d record(s); waiting for indexing", len(records))
    client.wait_for_completed_records(
        kb_id=kb_id, expected_min=1, timeout=int(os.getenv("SDK_KB_INDEX_TIMEOUT", "420"))
    )
    return kb_id


# Extensions the knowledgeBase upload endpoint accepts. The sample-data set also
# contains code/config files (.js/.json/.py) which the backend rejects with
# "Invalid file type", so we only pick from these document types.
_ALLOWED_SUFFIXES = {".pdf", ".txt", ".csv", ".doc", ".docx", ".xls", ".xlsx"}


def _pick_sample_files(limit: int) -> List[Path]:
    root = ensure_sample_data_files_root()
    files = [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix.lower() in _ALLOWED_SUFFIXES
    ][:limit]
    if not files:
        raise RuntimeError(f"No supported sample files found under {root}")
    return files


def export_env(values: Dict[str, str], secret_keys: tuple[str, ...] = ()) -> None:
    """Emit env vars to $GITHUB_ENV, stdout, and an optional .env file.

    ``secret_keys`` are masked in the GitHub Actions log via ``::add-mask::``.
    """
    # Mask secrets in the Actions log before printing anything containing them.
    for key in secret_keys:
        val = values.get(key)
        if val:
            print(f"::add-mask::{val}")

    lines = [f"{k}={v}" for k, v in values.items()]

    github_env = os.getenv("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        log.info("Appended %d var(s) to $GITHUB_ENV", len(lines))

    env_path = os.getenv("SDK_TEST_ENV_PATH")
    if env_path:
        path = Path(env_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        log.info("Wrote SDK env file: %s", path)

    # Echo to stdout (secrets already registered for masking above).
    for k, v in values.items():
        printed = "***" if k in secret_keys else v
        print(f"{k}={printed}")


def main() -> int:
    base_url = (os.getenv("PIPESHUB_BASE_URL") or "http://localhost:3000").rstrip("/")
    if not (os.getenv("PIPESHUB_TEST_USER_EMAIL") and os.getenv("PIPESHUB_TEST_USER_PASSWORD")):
        log.error("PIPESHUB_TEST_USER_EMAIL and PIPESHUB_TEST_USER_PASSWORD are required")
        return 2
    if not _api_key():
        log.error("TEST_OPENAI_API_KEY (or OPENAI_API_KEY) is required to configure the LLM")
        return 2

    client_id, client_secret = mint_oauth_app(base_url)
    client = PipeshubClient(base_url=base_url)

    seed_llm(client)
    kb_id = seed_kb(client)

    export_env(
        {
            "PIPESHUB_API_URL": f"{base_url}/api/v1",
            "PIPESHUB_TOKEN_URL": f"{base_url}/api/v1/oauth2/token",
            "PIPESHUB_CLIENT_ID": client_id,
            "PIPESHUB_CLIENT_SECRET": client_secret,
            "KB_ID": kb_id,
        },
        secret_keys=("PIPESHUB_CLIENT_SECRET",),
    )
    log.info("✅ Provisioning complete (KB=%s)", kb_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
