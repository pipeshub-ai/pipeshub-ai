"""The Confluence OAuth app keeps its "Grant Jira user access" setting.

The connector and the agent toolset both register an OAuth config named
"Confluence", and the registry keeps the one registered last. Startup loads
connectors first and toolsets second, and saving an OAuth app stores only the
fields the registry lists, so both registrations must declare includeJiraScope.
"""

import json
import subprocess
import sys
from importlib import import_module
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

JIRA_USER_SCOPE = "read:jira-user"
TOOLSET_SCOPES = ["read:confluence-content.all", "write:confluence-content"]

_BACKEND_ROOT = Path(__file__).resolve().parents[4]
_STARTUP_ORDER_SCRIPT = """
import asyncio, json, logging
from unittest.mock import AsyncMock
logging.disable(logging.CRITICAL)
import app.connectors.sources.atlassian.confluence_cloud.connector  # startup registers connectors first
from app.agents.registry.toolset_registry import get_toolset_registry
get_toolset_registry().discover_toolsets(["app.agents.actions.confluence.confluence"])  # then the toolsets
from app.connectors.api.router import _create_or_update_oauth_config
from app.connectors.core.registry.auth_utils import auth_field_to_dict
from app.connectors.core.registry.oauth_config_registry import get_oauth_config_registry
from app.connectors.sources.atlassian.confluence_cloud.connector import ConfluenceConnector

registered = {f.name: auth_field_to_dict(f) for f in get_oauth_config_registry().get_config("Confluence").auth_fields}
connector_fields = {
    f["name"]: f
    for f in ConfluenceConnector._connector_metadata["config"]["auth"]["schemas"]["OAUTH"]["fields"]
}

async def save(oauth_app_id, existing):
    service = AsyncMock()
    service.get_config = AsyncMock(return_value=existing)
    service.set_config = AsyncMock(return_value=True)
    await _create_or_update_oauth_config(
        connector_type="Confluence",
        auth_config={"clientId": "c", "clientSecret": "s", "includeJiraScope": "no"},
        instance_name="Confluence", user_id="u1", org_id="org-1", is_admin=True,
        config_service=service, base_url="https://pipeshub.example", oauth_app_id=oauth_app_id,
    )
    return service.set_config.call_args.args[1][-1]["config"].get("includeJiraScope")

existing = [{"_id": "app-1", "orgId": "org-1", "createdBy": "u1", "config": {"clientId": "c", "clientSecret": "s"}}]
toolset_schema = get_toolset_registry().get_toolset_metadata("confluence")["config"]["auth"]["schemas"]["OAUTH"]
print(json.dumps({
    "registered": registered.get("includeJiraScope"),
    "connector": connector_fields.get("includeJiraScope"),
    "saved_on_create": asyncio.run(save(None, [])),
    "saved_on_update": asyncio.run(save("app-1", existing)),
    "toolset_form": [f["name"] for f in toolset_schema["fields"]],
}))
"""


@pytest.fixture(scope="module")
def after_startup() -> dict:
    run = subprocess.run(
        [sys.executable, "-c", _STARTUP_ORDER_SCRIPT],
        cwd=_BACKEND_ROOT,
        capture_output=True,
        text=True,
        timeout=170,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-2000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


@pytest.mark.timeout(180)
class TestTheToolsetDoesNotDropTheSetting:
    """Imports in startup order in a fresh process: the connector, then the toolset."""

    def test_the_registry_still_lists_it(self, after_startup: dict) -> None:
        assert after_startup["registered"] is not None

    def test_the_toolset_registers_the_connectors_definition(self, after_startup: dict) -> None:
        # The OAuth apps page shows the registered field, so it must read as the connector's does.
        assert after_startup["registered"] == after_startup["connector"]

    def test_creating_the_oauth_app_saves_it(self, after_startup: dict) -> None:
        assert after_startup["saved_on_create"] == "no"

    def test_updating_the_oauth_app_saves_it(self, after_startup: dict) -> None:
        assert after_startup["saved_on_update"] == "no"

    def test_the_toolset_form_offers_it(self, after_startup: dict) -> None:
        assert "includeJiraScope" in after_startup["toolset_form"]


class TestToolsetSignIn:
    """The toolset's own consent request honours the setting saved on its OAuth app."""

    def _registry_oauth(self) -> MagicMock:
        oauth = MagicMock()
        oauth.redirect_uri = "toolsets/oauth/callback/confluence"
        oauth.authorize_url = "https://auth.atlassian.com/authorize"
        oauth.token_url = "https://auth.atlassian.com/oauth/token"
        oauth.additional_params = {}
        oauth.token_access_type = None
        oauth.scope_parameter_name = "scope"
        oauth.token_response_path = None
        oauth.scopes.get_scopes_for_type = MagicMock(return_value=list(TOOLSET_SCOPES))
        return oauth

    async def _scopes(self, toolset_type: str = "confluence", **app_settings: object) -> list[str]:
        import_module("app.connectors.api.router")  # first: toolsets imported alone hits an import cycle
        from app.api.routes import toolsets

        app_config = {"type": "OAUTH", "clientId": "c", "clientSecret": "s", **app_settings}
        with patch.object(toolsets, "_get_oauth_config_from_registry", return_value=self._registry_oauth()):
            flow = await toolsets._build_oauth_config(app_config, toolset_type, MagicMock(), "https://pipeshub.example")
        return flow["scopes"]

    async def test_yes_asks_for_the_jira_user_scope(self) -> None:
        assert await self._scopes(includeJiraScope="yes") == [*TOOLSET_SCOPES, JIRA_USER_SCOPE]

    async def test_yes_also_applies_to_saved_scopes(self) -> None:
        scopes = await self._scopes(includeJiraScope="yes", scopes=list(TOOLSET_SCOPES))
        assert scopes == [*TOOLSET_SCOPES, JIRA_USER_SCOPE]

    async def test_no_leaves_it_out(self) -> None:
        assert await self._scopes(includeJiraScope="no") == TOOLSET_SCOPES

    async def test_an_app_saved_before_the_setting_asks_for_what_it_did_before(self) -> None:
        assert await self._scopes() == TOOLSET_SCOPES

    async def test_other_toolsets_ignore_it(self) -> None:
        assert await self._scopes("jira", includeJiraScope="yes") == TOOLSET_SCOPES
