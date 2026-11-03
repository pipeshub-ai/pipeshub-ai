from typing import Any

from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.auth_utils import include_jira_scope_enabled
from app.connectors.core.registry.types import AuthField
from app.connectors.sources.atlassian.core.oauth import AtlassianScope

INCLUDE_JIRA_SCOPE = "includeJiraScope"
INCLUDE_JIRA_SCOPE_DESCRIPTION = (
    "Choose Yes only if your Atlassian OAuth app includes Jira and you have added the "
    "read:jira-user scope. Pipeshub will request that scope during authorization and may "
    "use Jira to resolve user emails when Confluence profiles hide them. Choose No if you "
    "do not use Jira on this site or have not added that scope."
)


def confluence_include_jira_scope_field(*, default_value: str = "yes") -> AuthField:
    """The "Grant Jira user access" choice on a Confluence Cloud OAuth app.

    The connector and the agent toolset each register their own OAuth config
    named "Confluence". Each side's OAuth app form shows its own registration's
    fields, and a connector OAuth app saves only the connector's, so both declare
    this field. Only the pre-filled choice differs between them.
    """
    return AuthField(
        name=INCLUDE_JIRA_SCOPE,
        display_name="Grant Jira user access",
        description=INCLUDE_JIRA_SCOPE_DESCRIPTION,
        field_type="SELECT",
        required=True,
        placeholder="Select...",
        default_value=default_value,
        options=["no", "yes"],
        usage="CONFIGURE",
        is_secret=False,
    )


def apply_confluence_jira_scope(
    connector_type: str,
    settings: dict[str, Any],
    scopes: list[str],
) -> list[str]:
    """Add or remove read:jira-user on a Confluence Cloud consent request, per includeJiraScope.

    A missing setting counts as No. Other connector and toolset types are left alone.
    """
    if (connector_type or "").replace(" ", "").upper() != Connectors.CONFLUENCE.value:
        return scopes
    jira_scope = AtlassianScope.JIRA_USER_READ.value
    if include_jira_scope_enabled(settings.get(INCLUDE_JIRA_SCOPE)):
        return scopes if jira_scope in scopes else [*scopes, jira_scope]
    return [scope for scope in scopes if scope != jira_scope]


ALLOW_FILE_UPLOADS = "allowFileUploads"
ALLOW_FILE_UPLOADS_DESCRIPTION = (
    "Choose Yes only if your Atlassian OAuth app includes the write:attachment:confluence "
    "scope. Pipeshub will request that scope during authorization so agents can attach "
    "files to pages. Choose No if you have not added that scope: Atlassian refuses sign-in "
    "when an app is asked for a scope it does not have."
)


def confluence_allow_file_uploads_field(*, default_value: str = "no") -> AuthField:
    """The "Allow file uploads" choice on the Confluence agent toolset's OAuth app.

    Not required, so OAuth apps saved before the choice existed keep working as No.
    """
    return AuthField(
        name=ALLOW_FILE_UPLOADS,
        display_name="Allow file uploads",
        description=ALLOW_FILE_UPLOADS_DESCRIPTION,
        field_type="SELECT",
        required=False,
        placeholder="Select...",
        default_value=default_value,
        options=["no", "yes"],
        usage="CONFIGURE",
        is_secret=False,
    )


def apply_confluence_upload_scope(
    toolset_type: str,
    settings: dict[str, Any],
    scopes: list[str],
) -> list[str]:
    """Add or remove write:attachment:confluence on a Confluence consent request, per allowFileUploads.

    A missing setting counts as No. Other toolset types are left alone.
    """
    if (toolset_type or "").replace(" ", "").upper() != Connectors.CONFLUENCE.value:
        return scopes
    upload_scope = AtlassianScope.CONFLUENCE_ATTACHMENT_WRITE.value
    if include_jira_scope_enabled(settings.get(ALLOW_FILE_UPLOADS)):
        return scopes if upload_scope in scopes else [*scopes, upload_scope]
    return [scope for scope in scopes if scope != upload_scope]
