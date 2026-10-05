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


def confluence_include_jira_scope_field() -> AuthField:
    """The "Grant Jira user access" choice on a Confluence Cloud OAuth app.

    The connector and the agent toolset both register an OAuth config named
    "Confluence", and the later registration replaces the earlier one, so both
    must declare this field or it drops out of the saved OAuth app.
    """
    return AuthField(
        name=INCLUDE_JIRA_SCOPE,
        display_name="Grant Jira user access",
        description=INCLUDE_JIRA_SCOPE_DESCRIPTION,
        field_type="SELECT",
        required=True,
        placeholder="Select...",
        default_value="yes",
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
