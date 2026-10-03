"""Fixtures for running the pipeshub-ai/examples repository against this stack.

The examples live in their own repository, so nothing else notices when a change
here breaks them. These fixtures check the examples out and give them what a
reader would have: the stack's URL, a personal access token minted the way the
tutorials tell people to, and a document to find.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import requests

from helper.clients.kb_client import KBClient
from helper.indexing_progress import wait_until_finished
from helper.local_auth import obtain_user_session_token
from helper.stored_names import stored_name

logger = logging.getLogger("examples")

EXAMPLES_REPO = "https://github.com/pipeshub-ai/examples.git"
# Which examples to test. The default is what readers get; the examples
# repository's own checks point these at the branch or checkout under review.
EXAMPLES_REF_ENV = "PIPESHUB_EXAMPLES_REF"
EXAMPLES_DIR_ENV = "PIPESHUB_EXAMPLES_DIR"

@pytest.fixture(scope="session")
def examples_base_url() -> str:
    base_url = os.getenv("PIPESHUB_BASE_URL", "").rstrip("/")
    if not base_url:
        pytest.skip("PIPESHUB_BASE_URL is not set")
    return base_url


@pytest.fixture(scope="session")
def examples_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The examples checkout, with the commit under test logged.

    The repository moves without anything here changing, so when a test starts
    failing the commit is the difference between an obvious cause and a mystery.
    """
    local = os.getenv(EXAMPLES_DIR_ENV, "").strip()
    if local:
        path = Path(local).resolve()
    else:
        if shutil.which("git") is None:
            pytest.skip("git is not on PATH, so the examples cannot be checked out")
        ref = os.getenv(EXAMPLES_REF_ENV, "").strip() or "main"
        path = tmp_path_factory.mktemp("examples") / "examples"
        subprocess.run(
            ["git", "clone", "--quiet", "--depth", "1", "--branch", ref, EXAMPLES_REPO, str(path)],
            check=True, timeout=120,
        )
    commit = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip()
    logger.info("Testing pipeshub-ai/examples at %s (%s)", commit or "unknown commit", path)
    return path


@pytest.fixture(scope="session")
def examples_user_jwt(examples_base_url: str) -> str:
    if not (os.getenv("PIPESHUB_TEST_USER_EMAIL") and os.getenv("PIPESHUB_TEST_USER_PASSWORD")):
        pytest.skip("PIPESHUB_TEST_USER_EMAIL / PIPESHUB_TEST_USER_PASSWORD are not set")
    return obtain_user_session_token(examples_base_url)


@pytest.fixture(scope="session")
def examples_token(examples_base_url: str, examples_user_jwt: str) -> Iterator[str]:
    """A personal access token, minted as the tutorials say: default scopes, own user.

    Every example authenticates this way, so a change to how these tokens are
    minted or accepted breaks all of them at once.
    """
    headers = {"Authorization": f"Bearer {examples_user_jwt}"}
    resp = requests.post(
        f"{examples_base_url}/api/v1/personal-access-tokens",
        json={"name": f"integration-examples-{uuid.uuid4().hex[:12]}", "expiryDays": 30},
        headers=headers, timeout=30,
    )
    assert resp.status_code == 201, f"minting a personal access token failed: {resp.status_code} {resp.text[:500]}"
    token = resp.json()["token"]
    try:
        yield token["accessToken"]
    finally:
        # In a finally, so a failing test still revokes it: this suite can run
        # against a long-lived instance, where a leaked token stays valid.
        try:
            requests.delete(
                f"{examples_base_url}/api/v1/personal-access-tokens/{token['id']}",
                headers=headers, timeout=30,
            )
        except Exception:  # noqa: BLE001 - teardown must not mask test results
            logger.warning("could not revoke the examples token %s", token["id"])


@pytest.fixture(scope="session")
def seeded_record(kb_client: KBClient, ai_models_configured) -> Iterator[dict[str, str]]:
    """A document put here by this test, with a phrase nothing else contains.

    An example that lists it can only have done so if upload, indexing, embedding
    and the example's own search all worked during this run.

    Takes ``ai_models_configured`` because indexing needs an organisation LLM and
    embedding model, and the examples' chat needs the LLM.
    """
    token = uuid.uuid4().hex[:12]
    needle = f"quillfeather {token} reconciliation ledger"
    name = f"examples-{token}.md"
    body = (
        f"# Ledger runbook {token}\n\n"
        f"The {needle} is closed on the third working day of each month by the "
        f"finance operations team, after the {needle} has been checked twice.\n\n"
        + "\n\n".join(
            f"## Step {n}\n\nReconciliation note {n} for ledger {token}. " + "Routine detail. " * 12
            for n in range(1, 11)
        )
    ).encode()

    kb_name = f"examples-{token}"
    kb_id = kb_client.create_kb(kb_name)["id"]
    try:
        upload = kb_client.upload_file(kb_id, name, body, mimetype="text/markdown")
        record_id = upload["records"][0]["recordId"]

        final = asyncio.run(wait_until_finished(kb_client, [record_id]))
        if final.get(record_id) != "COMPLETED":
            pytest.skip(
                f"the seeded document did not finish indexing ({final.get(record_id)}), "
                "so there is nothing for the examples to find; this is a stack problem"
            )

        yield {
            "record_id": record_id,
            "name": stored_name(name),
            "needle": needle,
            # How a reader would ask for it.
            "question": f"When is the {needle} closed, and by whom?",
        }
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception:  # noqa: BLE001 - teardown must not mask test results
            logger.warning("could not delete the seeded knowledge base %s", kb_id)
