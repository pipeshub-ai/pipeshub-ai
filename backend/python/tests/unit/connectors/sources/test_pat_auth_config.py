"""A personal access token entered in the connector form must reach the client.

The connector's API_TOKEN form stores each field under its own name, with no
OAuth credentials, and the client builder reads the token by a fixed key. If
the two disagree, a configured token is silently dropped and the connector
fails to start with "Token required" — so these tests build the stored config
from the connector's own registered schema rather than from a hand-written one.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.github_teams.connector import GitHubTeamsConnector
from app.connectors.sources.gitlab.connector import GitLabConnector
from app.sources.client.github.github import GitHubClient
from app.sources.client.gitlab.gitlab import GitLabClient

TOKEN = "pat-value"


def _stored_token_config(connector_cls: type) -> dict:
    """The config a token-only setup stores: the secret field under its name, no credentials."""
    auth = connector_cls._connector_metadata["config"]["auth"]
    assert "API_TOKEN" in auth["supportedAuthTypes"]
    secret_fields = [f["name"] for f in auth["schemas"]["API_TOKEN"]["fields"] if f["isSecret"]]
    assert len(secret_fields) == 1, f"expected one secret field, got {secret_fields}"
    return {"auth": {"authType": "API_TOKEN", secret_fields[0]: TOKEN}}


def _config_service(config: dict) -> MagicMock:
    service = MagicMock()
    service.get_config = AsyncMock(return_value=config)
    return service


@pytest.mark.asyncio
async def test_github_teams_token_reaches_client() -> None:
    service = _config_service(_stored_token_config(GitHubTeamsConnector))
    with patch("app.sources.client.github.github.Github"), \
         patch("app.sources.client.github.github.Auth"):
        client = await GitHubClient.build_from_services(MagicMock(), service, "inst-1")
    assert client.get_token() == TOKEN


@pytest.mark.asyncio
async def test_gitlab_token_reaches_client() -> None:
    service = _config_service(_stored_token_config(GitLabConnector))
    with patch("app.sources.client.gitlab.gitlab.gitlab"):
        client = await GitLabClient.build_from_services(MagicMock(), service, "inst-1")
    assert client.get_token() == TOKEN
