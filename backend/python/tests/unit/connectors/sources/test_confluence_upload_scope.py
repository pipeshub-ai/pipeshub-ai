"""The Confluence toolset asks for the attachment upload scope only when its OAuth app opts in.

Atlassian refuses sign-in when an app is asked for a scope it hasn't been given, so
write:attachment:confluence is requested only for apps saved with "Allow file uploads" = yes.
Apps saved before the setting existed, or with the form's default, ask for what they did before.
"""

import sys
from importlib import import_module
from types import ModuleType
from unittest.mock import MagicMock, patch

from app.connectors.sources.atlassian.core.auth_fields import (
    ALLOW_FILE_UPLOADS,
    apply_confluence_upload_scope,
    confluence_allow_file_uploads_field,
)

UPLOAD_SCOPE = "write:attachment:confluence"
JIRA_USER_SCOPE = "read:jira-user"
TOOLSET_SCOPES = ["read:confluence-content.all", "write:confluence-content"]

_TOOLSETS = "app.api.routes.toolsets"


def _real_toolsets_module() -> ModuleType:
    """The routes module itself, even when another test file put a stub in sys.modules.

    Some datasource tests stand a stub in for it to skip its heavy imports; it stays for
    the rest of the session, so this test would otherwise run against the stub.
    """
    stub = sys.modules.get(_TOOLSETS)
    if stub is not None and getattr(stub, "__file__", None) is None:
        del sys.modules[_TOOLSETS]
    try:
        import_module("app.connectors.api.router")  # first: toolsets imported alone hits an import cycle
        return import_module(_TOOLSETS)
    finally:
        if stub is not None and getattr(stub, "__file__", None) is None:
            sys.modules[_TOOLSETS] = stub


class TestApplyConfluenceUploadScope:
    def test_yes_adds_the_scope_once(self) -> None:
        assert apply_confluence_upload_scope("confluence", {ALLOW_FILE_UPLOADS: "yes"}, TOOLSET_SCOPES) == [
            *TOOLSET_SCOPES, UPLOAD_SCOPE,
        ]
        with_scope = [*TOOLSET_SCOPES, UPLOAD_SCOPE]
        assert apply_confluence_upload_scope("Confluence", {ALLOW_FILE_UPLOADS: "Yes"}, with_scope) == with_scope

    def test_no_or_missing_leaves_it_out(self) -> None:
        assert apply_confluence_upload_scope("confluence", {ALLOW_FILE_UPLOADS: "no"}, TOOLSET_SCOPES) == TOOLSET_SCOPES
        assert apply_confluence_upload_scope("confluence", {}, TOOLSET_SCOPES) == TOOLSET_SCOPES
        assert apply_confluence_upload_scope("confluence", {}, [*TOOLSET_SCOPES, UPLOAD_SCOPE]) == TOOLSET_SCOPES

    def test_other_toolsets_are_left_alone(self) -> None:
        scopes = [*TOOLSET_SCOPES, UPLOAD_SCOPE]
        assert apply_confluence_upload_scope("jira", {ALLOW_FILE_UPLOADS: "no"}, scopes) == scopes
        assert apply_confluence_upload_scope("Confluence Data Center", {ALLOW_FILE_UPLOADS: "yes"}, TOOLSET_SCOPES) == TOOLSET_SCOPES

    def test_field_defaults_to_no_and_is_optional(self) -> None:
        field = confluence_allow_file_uploads_field()
        assert field.name == ALLOW_FILE_UPLOADS
        assert field.default_value == "no"
        assert field.required is False
        assert field.options == ["no", "yes"]


class TestToolsetSignIn:
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
        toolsets = _real_toolsets_module()

        app_config = {"type": "OAUTH", "clientId": "c", "clientSecret": "s", **app_settings}
        with patch.object(toolsets, "_get_oauth_config_from_registry", return_value=self._registry_oauth()):
            flow = await toolsets._build_oauth_config(app_config, toolset_type, MagicMock(), "https://pipeshub.example")
        return flow["scopes"]

    async def test_yes_asks_for_the_upload_scope(self) -> None:
        assert await self._scopes(allowFileUploads="yes") == [*TOOLSET_SCOPES, UPLOAD_SCOPE]

    async def test_an_app_saved_before_the_setting_asks_for_what_it_did_before(self) -> None:
        assert await self._scopes() == TOOLSET_SCOPES

    async def test_the_forms_default_asks_for_what_it_did_before(self) -> None:
        assert await self._scopes(allowFileUploads="no", includeJiraScope="no") == TOOLSET_SCOPES

    async def test_works_alongside_the_jira_user_scope(self) -> None:
        assert await self._scopes(allowFileUploads="yes", includeJiraScope="yes") == [
            *TOOLSET_SCOPES, JIRA_USER_SCOPE, UPLOAD_SCOPE,
        ]

    async def test_jira_toolset_ignores_it(self) -> None:
        assert await self._scopes("jira", allowFileUploads="yes") == TOOLSET_SCOPES
