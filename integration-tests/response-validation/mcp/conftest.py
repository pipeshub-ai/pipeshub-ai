"""
Fixtures for the MCP server surface tests.

One OAuth app with every scope and every grant type is created for the
session and deleted at the end. One MCP session reads the surface once;
every test asserts against that dict.

The expected surface lives in ``golden/mcp_surface_<version>.json`` where
``<version>`` is the ``@pipeshub-ai/mcp`` pin in
``backend/nodejs/apps/package.json``. Bumping the pin therefore needs a new
golden, generated with ``pytest response-validation/mcp --update-mcp-golden``.

The tool tests call every tool with the authorization_code token from the
same app, against a document and an agent created for the session.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any, Iterator

import pytest

from helper.clients.agents_client import AgentsClient
from helper.clients.kb_client import KBClient
from helper.indexing_progress import wait_until_finished
from helper.local_auth import obtain_user_session_token
from helper.mcp_client import read_mcp_surface
from helper.mcp_oauth import (
    OAuthApp,
    authorization_code_token,
    client_credentials_token,
    create_oauth_app,
    delete_oauth_app,
)
from helper.mcp_pin import mcp_package_pin
from helper.stored_names import stored_name

logger = logging.getLogger("mcp-tools")

_THIS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _THIS_DIR.parents[2]
GOLDEN_DIR = _THIS_DIR / "golden"


@pytest.fixture(scope="session")
def mcp_base_url() -> str:
    base_url = os.getenv("PIPESHUB_BASE_URL", "").rstrip("/")
    if not base_url:
        pytest.skip("PIPESHUB_BASE_URL is not set")
    return base_url


@pytest.fixture(scope="session")
def mcp_user_jwt(mcp_base_url: str) -> str:
    if not (os.getenv("PIPESHUB_TEST_USER_EMAIL") and os.getenv("PIPESHUB_TEST_USER_PASSWORD")):
        pytest.skip("PIPESHUB_TEST_USER_EMAIL / PIPESHUB_TEST_USER_PASSWORD are not set")
    return obtain_user_session_token(mcp_base_url)


@pytest.fixture(scope="session")
def mcp_oauth_app(mcp_base_url: str, mcp_user_jwt: str) -> Iterator[OAuthApp]:
    """Full-access OAuth app, as a host would register. Deleted after the session."""
    app = create_oauth_app(
        mcp_base_url,
        mcp_user_jwt,
        name=f"integration-mcp-surface-{uuid.uuid4().hex[:12]}",
    )
    try:
        yield app
    finally:
        try:
            delete_oauth_app(mcp_base_url, mcp_user_jwt, app.id)
        except Exception:  # noqa: BLE001 - teardown must not mask test results
            pass


@pytest.fixture(scope="session")
def mcp_access_token(mcp_base_url: str, mcp_user_jwt: str, mcp_oauth_app: OAuthApp) -> str:
    """Authorization-code + PKCE token: the token a real MCP host holds."""
    return authorization_code_token(mcp_base_url, mcp_user_jwt, mcp_oauth_app)["access_token"]


@pytest.fixture(scope="session")
def mcp_client_credentials_token(mcp_base_url: str, mcp_oauth_app: OAuthApp) -> str:
    return client_credentials_token(mcp_base_url, mcp_oauth_app)["access_token"]


@pytest.fixture(scope="session")
def mcp_seeded_record(kb_client: KBClient, ai_models_configured) -> Iterator[dict[str, str]]:
    """An indexed document with a phrase nothing else contains, in a collection of its own.

    The tools can only find, read, download and cite it if upload, indexing,
    embedding and retrieval all worked during this run.
    """
    token = uuid.uuid4().hex[:12]
    needle = f"quillfeather {token} rollback drill"
    name = f"mcp-tools-{token}.md"
    body = (
        f"# Release runbook {token}\n\n"
        f"The {needle} is owned by the platform reliability team. It runs every "
        f"second Tuesday, and the {needle} must finish within 45 minutes.\n\n"
        "## Steps\n\nFreeze deploys, roll back the canary, then confirm the error budget.\n"
    ).encode()

    kb_name = f"mcp-tools-{token}"
    kb_id = kb_client.create_kb(kb_name)["id"]
    try:
        upload = kb_client.upload_file(kb_id, name, body, mimetype="text/markdown")
        record_id = upload["records"][0]["recordId"]
        final = asyncio.run(wait_until_finished(kb_client, [record_id]))
        assert final.get(record_id) == "COMPLETED", (
            f"the seeded document did not finish indexing ({final.get(record_id)}), "
            "so the tools have nothing to find"
        )
        yield {
            "kb_id": kb_id,
            "kb_name": kb_name,
            "record_id": record_id,
            "name": stored_name(name),
            "needle": needle,
            "body": body.decode(),
        }
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception:  # noqa: BLE001 - teardown must not mask test results
            logger.warning("could not delete the seeded knowledge base %s", kb_id)


@pytest.fixture(scope="session")
def mcp_seeded_agent(
    agents_client: AgentsClient, ai_models_configured, mcp_seeded_record: dict[str, str]
) -> Iterator[dict[str, Any]]:
    """An agent with the seeded collection as its knowledge, for ``pipeshub_agents`` to list.

    It is never run, so the seeded LLM stands in for the reasoning model an
    agent must name to be created.
    """
    name = f"mcp-tools-agent-{uuid.uuid4().hex[:8]}"
    resp = agents_client.create_agent(
        name=name,
        description="Answers release runbook questions for the MCP tool tests.",
        models=[{
            "modelKey": ai_models_configured.model_key,
            "modelName": ai_models_configured.model_name,
            "provider": ai_models_configured.provider,
            "isReasoning": True,
        }],
        knowledge=[{"connectorId": mcp_seeded_record["kb_id"], "filters": {}}],
    )
    assert resp.status_code in (200, 201), f"creating the agent: {resp.status_code} {resp.text[:500]}"
    agent_key = (resp.json().get("agent") or {}).get("_key")
    assert agent_key, f"agent create returned no agent._key: {resp.text[:500]}"
    try:
        yield {"agent_id": agent_key, "name": name}
    finally:
        deleted = agents_client.delete_agent(agent_key)
        if deleted.status_code >= 300 and deleted.status_code != 404:
            logger.warning("could not delete agent %s: %s", agent_key, deleted.text[:300])


@pytest.fixture(scope="session")
def mcp_surface(mcp_base_url: str, mcp_access_token: str) -> dict:
    return read_mcp_surface(mcp_base_url, mcp_access_token)


@pytest.fixture(scope="session")
def mcp_golden_path() -> Path:
    return GOLDEN_DIR / f"mcp_surface_{mcp_package_pin()}.json"


@pytest.fixture(scope="session")
def mcp_golden(request: pytest.FixtureRequest, mcp_golden_path: Path, mcp_surface: dict) -> dict:
    """
    The expected surface. With ``--update-mcp-golden`` the live surface is
    written first, so a run that regenerates the golden also passes.
    """
    if request.config.getoption("--update-mcp-golden"):
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        mcp_golden_path.write_text(
            json.dumps(mcp_surface, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        )
    if not mcp_golden_path.exists():
        pytest.fail(
            f"No golden surface for @pipeshub-ai/mcp {mcp_package_pin()} at "
            f"{mcp_golden_path.relative_to(_REPO_ROOT)}. Review the served text, then run: "
            "pytest response-validation/mcp --update-mcp-golden"
        )
    return json.loads(mcp_golden_path.read_text())
