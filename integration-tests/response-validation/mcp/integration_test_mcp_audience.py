"""
MCP audience-bound tokens — integration tests
=============================================

An MCP host asks for its token with ``resource=<instance>/mcp`` (RFC 8707), so the token names
``/mcp`` as its audience. Such a token works only at ``/mcp``: a tool call it makes reaches the
REST API and the Python services, but the same token sent to either of them directly is refused.

``pipeshub_sources`` is the tool used here: it reads the knowledge hub from the connector service,
which asks Node to confirm the token, and needs no AI model or indexed document.
"""

from __future__ import annotations

import base64
import json

import pytest
import requests

from helper.connector_service import connector_service_url
from helper.mcp_client import call_mcp_tool
from helper.mcp_oauth import OAuthApp, authorization_code_token

pytestmark = [pytest.mark.integration, pytest.mark.mcp]

KNOWLEDGE_HUB_NODES = "/api/v1/knowledge-hub/nodes"


@pytest.fixture(scope="module")
def mcp_resource(mcp_base_url: str) -> str:
    resp = requests.get(f"{mcp_base_url}/.well-known/oauth-protected-resource/mcp", timeout=30)
    resp.raise_for_status()
    return resp.json()["resource"]


@pytest.fixture(scope="module")
def mcp_audience_token(mcp_base_url: str, mcp_user_jwt: str, mcp_oauth_app: OAuthApp, mcp_resource: str) -> str:
    return authorization_code_token(mcp_base_url, mcp_user_jwt, mcp_oauth_app, resource=mcp_resource)["access_token"]


def _claims(token: str) -> dict:
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_the_token_names_mcp_as_its_audience(mcp_audience_token: str, mcp_resource: str) -> None:
    assert _claims(mcp_audience_token)["aud"] == mcp_resource


def test_a_tool_that_goes_through_python_works(mcp_base_url: str, mcp_audience_token: str) -> None:
    result = call_mcp_tool(mcp_base_url, mcp_audience_token, "pipeshub_sources", {})
    assert not result.is_error, result.text
    assert "sources" in result.json()


def test_the_rest_api_refuses_it(mcp_base_url: str, mcp_audience_token: str) -> None:
    resp = requests.get(
        f"{mcp_base_url}/api/v1/knowledgeBase/knowledge-hub/nodes", headers=_bearer(mcp_audience_token), timeout=30
    )
    assert resp.status_code == 401, resp.text


def test_the_connector_service_refuses_it(mcp_base_url: str, mcp_audience_token: str, mcp_access_token: str) -> None:
    url = f"{connector_service_url(mcp_base_url)}{KNOWLEDGE_HUB_NODES}"
    # A token without an audience is accepted, so the refusal below is about the audience.
    assert requests.get(url, headers=_bearer(mcp_access_token), timeout=30).status_code == 200
    resp = requests.get(url, headers=_bearer(mcp_audience_token), timeout=30)
    assert resp.status_code == 401, resp.text
