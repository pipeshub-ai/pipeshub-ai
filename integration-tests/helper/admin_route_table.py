"""Every admin-only route, and how to call it as a member and as an admin.

``unit/test_admin_route_inventory.py`` compares this table with the routes
``helper/admin_routes.py`` finds in the source, so a route that gains an admin
check without a row here fails a test on every pull request. The live suite,
``permissions/test_admin_routes.py``, then calls each row as a signed-in member
(who must be refused) and as the admin (who must get past the check).

How the requests are shaped:

* Path parameters are filled with a well-formed id that exists nowhere, so a
  member's request is valid and reaches the admin check even on routes that
  validate before it.
* ``invalid`` admin requests replace every path parameter with ``not-an-id``
  and send ``[]`` as the body. Only used where a validator runs *after* the
  admin check, so the admin gets past the check and is then stopped by the
  validator before anything is written.
* ``same`` admin requests are the member's request unchanged: reads, and
  writes aimed at an id that does not exist.
* A row with ``admin=None`` says why the admin side is not called: passing the
  check there would change the shared test org (delete it, wipe the vector
  store, rewrite its sign-in settings). The member side still runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from helper import suite_extensions

# A well-formed Mongo ObjectId that no document has.
ABSENT_ID = "65f0c0ffee00000000000000"
INVALID_ID = "not-an-id"

# userAdminCheck and adminValidator both throw ForbiddenError with
# ADMIN_ACCESS_REQUIRED_MESSAGE (user_management/services/user-admin.service.ts).
NODE_REFUSAL_STATUS = 403
NODE_REFUSAL_MESSAGE = "You need admin access to do this. Ask an admin in your organisation."
# The wording Node used before #3696 (with HTTP 400); seeing it means that refusal is back.
OLD_NODE_REFUSAL_MESSAGE = "Admin access required"


@dataclass(frozen=True)
class AdminRoute:
    method: str
    path: str
    member_body: Any = field(default_factory=dict)
    # "same", "invalid", or None with ``why_no_admin`` filled in.
    admin: str | None = "same"
    why_no_admin: str = ""
    # Multipart upload instead of JSON (the org logo route checks the file first).
    upload: bool = False
    # For Python routes: the handler in backend/python/app this row covers,
    # and the words its refusal carries.
    handler: str = ""
    refusal_status: int = NODE_REFUSAL_STATUS
    refusal_words: str = NODE_REFUSAL_MESSAGE

    @property
    def key(self) -> tuple[str, str]:
        return self.method, self.path

    @property
    def id(self) -> str:
        return f"{self.method} {self.path}"

    def url_path(self, value: str) -> str:
        return re.sub(r":\w+|\{\w+\}", value, self.path)


def _no_admin(method: str, path: str, why: str, **kw: Any) -> AdminRoute:
    return AdminRoute(method, path, admin=None, why_no_admin=why, **kw)


_CM = "/api/v1/configurationManager"

NODE_ADMIN_ROUTES: tuple[AdminRoute, ...] = (
    # orgAuthConfig.routes.ts (adminValidator)
    AdminRoute("GET", "/api/v1/orgAuthConfig/authMethods"),
    AdminRoute("GET", "/api/v1/orgAuthConfig/signInRemoval"),
    _no_admin("POST", "/api/v1/orgAuthConfig",
              "sets the org's sign-in methods; nothing after the check stops a write"),
    AdminRoute("POST", "/api/v1/orgAuthConfig/updateAuthMethod", admin="invalid"),
    # configuration_manager/routes/cm_routes.ts
    AdminRoute("POST", f"{_CM}/storageConfig", admin="invalid"),
    AdminRoute("GET", f"{_CM}/storageConfig"),
    AdminRoute("POST", f"{_CM}/smtpConfig", admin="invalid"),
    AdminRoute("GET", f"{_CM}/connectors/atlassian/config"),
    AdminRoute("POST", f"{_CM}/connectors/atlassian/config", admin="invalid"),
    AdminRoute("GET", f"{_CM}/connectors/onedrive/config"),
    AdminRoute("GET", f"{_CM}/connectors/sharepoint/config"),
    AdminRoute("POST", f"{_CM}/connectors/sharepoint/config", admin="invalid"),
    AdminRoute("POST", f"{_CM}/connectors/onedrive/config", admin="invalid"),
    AdminRoute("GET", f"{_CM}/smtpConfig"),
    AdminRoute("GET", f"{_CM}/authConfig/microsoft"),
    AdminRoute("POST", f"{_CM}/authConfig/microsoft", admin="invalid"),
    AdminRoute("GET", f"{_CM}/authConfig/google"),
    AdminRoute("POST", f"{_CM}/authConfig/google", admin="invalid"),
    AdminRoute("POST", f"{_CM}/platform/settings", admin="invalid"),
    AdminRoute("GET", f"{_CM}/platform/settings"),
    AdminRoute("GET", f"{_CM}/platform/feature-flags/available"),
    AdminRoute("GET", f"{_CM}/secretReveal"),
    AdminRoute("GET", f"{_CM}/slack-bot"),
    AdminRoute("POST", f"{_CM}/slack-bot", admin="invalid"),
    AdminRoute("PUT", f"{_CM}/slack-bot/:configId", admin="invalid"),
    AdminRoute("DELETE", f"{_CM}/slack-bot/:configId", admin="invalid"),
    AdminRoute("GET", f"{_CM}/prompts/system"),
    _no_admin("PUT", f"{_CM}/prompts/system",
              "replaces the org's system prompt; nothing after the check stops a write"),
    _no_admin("POST", f"{_CM}/connectors/googleWorkspaceCredentials",
              "stores an uploaded credentials file for the org"),
    AdminRoute("GET", f"{_CM}/connectors/googleWorkspaceCredentials"),
    AdminRoute("GET", f"{_CM}/connectors/googleWorkspaceOauthConfig"),
    AdminRoute("POST", f"{_CM}/connectors/googleWorkspaceOauthConfig", admin="invalid"),
    AdminRoute("POST", f"{_CM}/aiModelsConfig", admin="invalid"),
    AdminRoute("GET", f"{_CM}/aiModelsConfig"),
    AdminRoute("GET", f"{_CM}/ai-models/registry/capabilities"),
    AdminRoute("GET", f"{_CM}/ai-models/registry/:providerId/schema"),
    AdminRoute("GET", f"{_CM}/ai-models/registry"),
    AdminRoute("GET", f"{_CM}/ai-models"),
    AdminRoute("GET", f"{_CM}/ai-models/roles"),
    _no_admin("PUT", f"{_CM}/ai-models/roles",
              "reassigns which model plays each role for the org; no validator follows the check"),
    AdminRoute("GET", f"{_CM}/ai-models/download-progress"),
    AdminRoute("GET", f"{_CM}/ai-models/:modelType", admin="invalid"),
    AdminRoute("POST", f"{_CM}/ai-models/providers", admin="invalid"),
    AdminRoute("POST", f"{_CM}/ai-models/discover", admin="invalid"),
    AdminRoute("POST", f"{_CM}/ai-models/providers/batch", admin="invalid"),
    AdminRoute("PUT", f"{_CM}/ai-models/connections/:connectionId/credentials", admin="invalid"),
    _no_admin("POST", f"{_CM}/ai-models/prepare-model",
              "starts preparing a local model; no validator follows the check"),
    AdminRoute("PUT", f"{_CM}/ai-models/connections/:connectionId/identity-forwarding", admin="invalid"),
    AdminRoute("GET", f"{_CM}/ai-models/identity-policy"),
    AdminRoute("PUT", f"{_CM}/ai-models/identity-policy", admin="invalid"),
    AdminRoute("POST", f"{_CM}/ai-models/identity/preview", admin="invalid"),
    AdminRoute("PUT", f"{_CM}/ai-models/providers/:modelType/:modelKey", admin="invalid"),
    AdminRoute("DELETE", f"{_CM}/ai-models/providers/:modelType/:modelKey", admin="invalid"),
    AdminRoute("PUT", f"{_CM}/ai-models/default/:modelType/:modelKey", admin="invalid"),
    AdminRoute("PUT", f"{_CM}/web-search/settings", admin="invalid"),
    AdminRoute("POST", f"{_CM}/web-search/providers", admin="invalid"),
    AdminRoute("PUT", f"{_CM}/web-search/providers/:providerKey", admin="invalid"),
    AdminRoute("DELETE", f"{_CM}/web-search/providers/:providerKey", admin="invalid"),
    AdminRoute("PUT", f"{_CM}/web-search/default/:providerKey", admin="invalid"),
    AdminRoute("POST", f"{_CM}/frontendPublicUrl", admin="invalid"),
    AdminRoute("POST", f"{_CM}/connectorPublicUrl", admin="invalid"),
    AdminRoute("PUT", f"{_CM}/metricsCollection/toggle", admin="invalid"),
    AdminRoute("GET", f"{_CM}/metricsCollection"),
    AdminRoute("PATCH", f"{_CM}/metricsCollection/pushInterval", admin="invalid"),
    AdminRoute("PATCH", f"{_CM}/metricsCollection/serverUrl", admin="invalid"),
    # crawling_manager/routes/cm_routes.ts
    AdminRoute("GET", "/api/v1/crawlingManager/schedule/all"),
    _no_admin("DELETE", "/api/v1/crawlingManager/schedule/all",
              "removes every sync schedule in the org"),
    # oauth_provider routes
    AdminRoute("PUT", "/api/v1/oauth-clients/:appId/token-identity", admin="invalid"),
    AdminRoute("GET", "/api/v1/personal-access-tokens/admin"),
    AdminRoute("DELETE", "/api/v1/personal-access-tokens/admin/:tokenId", admin="invalid"),
    AdminRoute("GET", "/api/v1/service-tokens"),
    AdminRoute("POST", "/api/v1/service-tokens", admin="invalid"),
    AdminRoute("GET", "/api/v1/service-tokens/scopes"),
    AdminRoute("DELETE", "/api/v1/service-tokens/:tokenId", admin="invalid"),
    # No rows for /api/v1/service-account-delegations, deliberately. Letting a
    # service account act as a named person is an Enterprise capability; this
    # build mounts a stub that answers the capability probe and 501s the rest
    # (oauth_provider/routes/delegation.routes.ts), with no admin check to
    # test. The Enterprise overlay carries the admin-gated router, so the rows
    # belong in whatever inventory covers that build, not this one.
    # tokens_manager/routes/connectors.routes.ts
    _no_admin("POST", "/api/v1/connectors/vector-store/cleanup",
              "drops and recreates the org's vector collection"),
    _no_admin("POST", "/api/v1/connectors/vector-store/reindex",
              "re-embeds every connector in the org"),
    _no_admin("POST", "/api/v1/connectors/getTokenFromCode",
              "exchanges an OAuth code and stores the result; no validator follows the check"),
    # tokens_manager/routes/health.routes.ts (read-only: estimates index memory)
    AdminRoute("GET", "/api/v1/health/vector-db/memory"),
    # user_management/routes/org.routes.ts
    AdminRoute("PUT", "/api/v1/org", admin="invalid"),
    _no_admin("DELETE", "/api/v1/org", "deletes the test org"),
    _no_admin("PUT", "/api/v1/org/logo",
              "replaces the org's logo (the file is checked before the admin check)",
              upload=True),
    _no_admin("DELETE", "/api/v1/org/logo", "removes the org's logo"),
    AdminRoute("PUT", "/api/v1/org/onboarding-status", admin="invalid"),
    # user_management/routes/service-accounts.routes.ts
    AdminRoute("GET", "/api/v1/service-accounts"),
    AdminRoute("POST", "/api/v1/service-accounts", admin="invalid"),
    AdminRoute("GET", "/api/v1/service-accounts/:id"),
    AdminRoute("PATCH", "/api/v1/service-accounts/:id", admin="invalid"),
    AdminRoute("DELETE", "/api/v1/service-accounts/:id", admin="invalid"),
    # user_management/routes/userGroups.routes.ts
    _no_admin("POST", "/api/v1/userGroups",
              "the body is validated before the admin check, so an admin request that "
              "reaches the check creates a group",
              member_body={"type": "custom", "name": "it-admin-probe"}),
    AdminRoute("GET", "/api/v1/userGroups"),
    AdminRoute("PUT", "/api/v1/userGroups/:groupId", admin="invalid"),
    AdminRoute("DELETE", "/api/v1/userGroups/:groupId", admin="invalid"),
    AdminRoute("POST", "/api/v1/userGroups/add-users", admin="invalid"),
    AdminRoute("POST", "/api/v1/userGroups/remove-users", admin="invalid"),
    # user_management/routes/users.routes.ts
    AdminRoute("GET", "/api/v1/users/:id/email"),
    AdminRoute("PUT", "/api/v1/users/:id/unblock"),
    _no_admin("POST", "/api/v1/users",
              "creates a user; every member in these suites is created through it as the admin",
              member_body={"fullName": "IT Admin Probe", "email": "it-admin-probe@test-pipeshub.com"}),
    AdminRoute("DELETE", "/api/v1/users/:id"),
    AdminRoute("GET", "/api/v1/users/:id/adminCheck"),
)


def _py(method: str, path: str, handler: str, **kw: Any) -> AdminRoute:
    kw.setdefault("refusal_status", 403)
    kw.setdefault("refusal_words", "admin")
    return AdminRoute(method, path, handler=handler, **kw)


_MCP = "/api/v1/mcp-servers"
_API_TOOLSETS = "/api/v1/api-toolsets"
_API_TOOLSET_BODY = {"name": "it-admin-probe", "baseUrl": "https://it-admin-probe.invalid", "operations": []}
_TOOLSETS = "/api/v1/toolsets"
_OAUTH_BODY = {"oauthInstanceName": "it-admin-probe", "config": {"clientId": "x"},
               "baseUrl": "http://localhost"}

# Python handlers that refuse every member, called through the Node gateway the
# way the web app calls them. Python answers 403; Node passes it through.
PYTHON_ADMIN_ROUTES: tuple[AdminRoute, ...] = (
    # Python checks the admin before it reads the body, so an admin sending an
    # empty config gets "config is required" and nothing is written.
    _py("POST", "/api/v1/oauth/:connectorType", "connectors/api/router.py::create_oauth_config",
        member_body=_OAUTH_BODY, admin="admin_body"),
    _py("PUT", "/api/v1/oauth/:connectorType/:configId",
        "connectors/api/router.py::update_oauth_config", member_body=_OAUTH_BODY),
    _py("DELETE", "/api/v1/oauth/:connectorType/:configId",
        "connectors/api/router.py::delete_oauth_config"),
    # Node refuses this one itself, with its own message, before proxying.
    _py("PUT", "/api/v1/knowledgeBase/demo-data/workspace",
        "modules/demo_data/router.py::set_demo_data_workspace",
        member_body={"enabled": False}, refusal_words="Only admins",
        admin=None, why_no_admin="turns the sample workspace on or off for the whole org"),
    _py("GET", f"{_MCP}/instances", "api/routes/mcp_servers.py::list_instances"),
    # An admin's request carries no usable spec, so the compiler stops it and nothing is saved.
    _py("POST", f"{_API_TOOLSETS}/validate", "api/routes/api_toolsets.py::validate_api_toolset_spec",
        member_body={"spec": "not a spec"}, admin="admin_body"),
    _py("POST", f"{_API_TOOLSETS}/instances", "api/routes/api_toolsets.py::create_api_toolset",
        member_body=_API_TOOLSET_BODY, admin="admin_body"),
    _py("GET", f"{_API_TOOLSETS}/instances/:instanceId/config", "api/routes/api_toolsets.py::get_api_toolset_config"),
    _py("PUT", f"{_API_TOOLSETS}/instances/:instanceId", "api/routes/api_toolsets.py::update_api_toolset",
        member_body=_API_TOOLSET_BODY),
    _py("DELETE", f"{_API_TOOLSETS}/instances/:instanceId", "api/routes/api_toolsets.py::delete_api_toolset"),
    _py("PUT", f"{_API_TOOLSETS}/instances/:instanceId/shared-credentials",
        "api/routes/api_toolsets.py::save_api_toolset_shared_credentials", member_body={"apiKey": "it-admin-probe"}),
    _py("DELETE", f"{_API_TOOLSETS}/instances/:instanceId/shared-credentials",
        "api/routes/api_toolsets.py::delete_api_toolset_shared_credentials"),
    _py("GET", f"{_API_TOOLSETS}/instances/:instanceId/shared-credentials/oauth/authorize",
        "api/routes/api_toolsets.py::start_api_toolset_shared_oauth"),
    _py("PUT", f"{_TOOLSETS}/oauth-configs/:toolsetType/:oauthConfigId",
        "api/routes/toolsets.py::update_toolset_oauth_config",
        member_body={"clientId": "x"}),
    _py("DELETE", f"{_TOOLSETS}/oauth-configs/:toolsetType/:oauthConfigId",
        "api/routes/toolsets.py::delete_toolset_oauth_config"),
)

# The admin body for rows marked ``admin="admin_body"``: past the admin check,
# each is refused by the handler's own input check before anything is stored.
ADMIN_BODIES: dict[tuple[str, str], Any] = {
    ("POST", "/api/v1/oauth/:connectorType"): {
        "oauthInstanceName": "it-admin-probe", "config": {}, "baseUrl": "http://localhost",
    },
    ("POST", f"{_API_TOOLSETS}/validate"): {"spec": "not a spec"},
    ("POST", f"{_API_TOOLSETS}/instances"): _API_TOOLSET_BODY,
}

# Python handlers that reach an admin check but sit behind Node's
# userAdminCheck, so the Node rows above already cover them.
PYTHON_BEHIND_NODE_ADMIN = frozenset({
    "connectors/api/router.py::cleanup_vector_store",
    "connectors/api/router.py::reindex_vector_store",
    "connectors_main.py::vector_db_memory_check",
})

# Python handlers that ask whether the caller is an admin but let members
# through for some inputs, so there is no single "member is refused" request.
# helper/admin_route_conditional_table.py holds the live cases for each one.
PYTHON_CONDITIONAL_ADMIN = {
    # Team-scoped connectors need an admin; a personal one is its creator's alone.
    "connectors/api/router.py::create_connector_instance": "team scope needs admin",
    "connectors/api/router.py::delete_connector_instance": "creator or admin",
    "connectors/api/router.py::get_connector_instance": "team connectors need admin",
    "connectors/api/router.py::get_connector_instance_config": "team connectors need admin",
    "connectors/api/router.py::get_connector_instance_filters": "team connectors need admin",
    "connectors/api/router.py::get_filter_field_options": "team connectors need admin",
    "connectors/api/router.py::save_connector_instance_filters": "team connectors need admin",
    "connectors/api/router.py::toggle_connector_instance": "team connectors need admin",
    "connectors/api/router.py::update_connector_instance_auth_config": "team connectors need admin",
    # Only when the check is for an instance being edited; a new connector's has no admin check.
    "connectors/api/router.py::check_connector_connection": "team connectors need admin",
    "connectors/api/router.py::update_connector_instance_config": "team connectors need admin",
    "connectors/api/router.py::update_connector_instance_filters_sync_config": "team connectors need admin",
    "connectors/api/router.py::update_connector_instance_name": "team connectors need admin",
    "connectors/api/router.py::get_oauth_authorization_url": "team connectors need admin",
    "connectors/api/router.py::handle_oauth_callback": "team connectors need admin",
    "connectors/api/router.py::reindex_connector": "team connectors need admin",
    "connectors/api/router.py::stop_connector_sync": "team connectors need admin",
    # Admin changes what is returned (all instances, unmasked secrets), never refuses.
    "connectors/api/router.py::get_connector_instances": "admin widens the listing",
    "connectors/api/router.py::get_configured_connector_instances": "admin widens the listing",
    "connectors/api/router.py::get_connector_registry": "same types for every caller",
    "connectors/api/router.py::get_active_agent_instances": "admin widens the listing",
    "connectors/api/router.py::get_all_oauth_configs": "no caller gets secrets",
    "connectors/api/router.py::list_oauth_configs": "secrets are masked for members",
    "connectors/api/router.py::get_oauth_config_by_id": "secrets are masked for members",
    "api/routes/toolsets.py::get_toolset_instances": "secrets are masked for members",
    "api/routes/toolsets.py::get_toolset_instance": "secrets are masked for members",
    "api/routes/toolsets.py::list_toolset_oauth_configs": "secrets are masked for members",
    # Everyone reads an org toolset's tool approvals; the admin flag says who may change them.
    # A personal toolset's are its owner's alone.
    "api/routes/toolsets.py::get_toolset_tool_rules": "members read, admins edit",
    # The listing is the same for an admin; the flag only labels the root `permissions` block.
    "connectors/sources/localKB/api/knowledge_hub_router.py::get_knowledge_hub_root_nodes":
        "admin flag fills the root permissions block",
    "connectors/sources/localKB/api/knowledge_hub_router.py::get_knowledge_hub_children_nodes":
        "admin flag is read but unused below a parent",
    # Members add toolsets of their own: an org toolset is the admins', a personal one its
    # owner's, and nobody else (the admin included) finds a personal one.
    "api/routes/toolsets.py::create_toolset_instance": "a member's new toolset is personal",
    "api/routes/toolsets.py::update_toolset_instance": "a personal toolset is its owner's",
    "api/routes/toolsets.py::delete_toolset_instance": "a personal toolset is its owner's",
    "api/routes/toolsets.py::update_toolset_tool_rules": "a personal toolset's rules are its owner's",
    # Record reads: the caller's own access to the record decides, admin or not.
    # The admin flag only reaches the connector loader, which refuses nobody on it.
    "connectors/api/router.py::download_file": "signed link and record permission",
    "connectors/api/router.py::stream_record": "record permission, admin or not",
    "connectors/api/router.py::stream_record_internal": "service token only",
    "connectors/api/router.py::get_record_content_internal": "service token only",
    # Only built-in skills need an admin to switch on or off; a member's own
    # skill is theirs alone.
    "api/routes/skills.py::disable_skill": "built-in skills need admin",
    "api/routes/skills.py::enable_skill": "built-in skills need admin",
    # Only instances that use the shared admin credential need an admin.
    "api/routes/mcp_servers.py::authenticate_instance": "shared admin credential needs admin",
    "api/routes/mcp_servers.py::update_credentials": "shared admin credential needs admin",
    "api/routes/mcp_servers.py::remove_credentials": "shared admin credential needs admin",
    "api/routes/mcp_servers.py::reauthenticate_instance": "shared admin credential needs admin",
    # Members add MCP servers of their own: an org server is the admins', a personal one
    # its owner's. The server is looked up before anyone is asked, so a missing id is a 404.
    "api/routes/mcp_servers.py::create_instance": "a member's new server is personal",
    "api/routes/mcp_servers.py::get_instance": "a personal server is its owner's",
    "api/routes/mcp_servers.py::update_instance": "a personal server is its owner's",
    "api/routes/mcp_servers.py::get_oauth_config": "a personal server is its owner's",
    "api/routes/mcp_servers.py::update_oauth_config": "a personal server is its owner's",
    "api/routes/mcp_servers.py::delete_instance": "a personal server is its owner's",
    # A member checks the URL of a server they could add: a personal one, public only.
    "api/routes/mcp_servers.py::discover_oauth_metadata_endpoint": "a member's URL must be public",
    # Everyone gets the listing; only admins see how an org server is reached.
    "api/routes/mcp_servers.py::get_my_mcp_servers": "connection details for admins",
    "api/routes/mcp_servers.py::get_agent_mcp_servers": "connection details for admins",
    # Members make and edit their own agents, and nothing here refuses them.
    # One field needs an admin: the service account the agent searches as,
    # because binding spends the access an administrator granted that account.
    "api/routes/agent.py::create_agent": "only a service-account binding needs admin",
    "api/routes/agent.py::update_agent": "only a service-account binding needs admin",
    # The directory of people. Everyone gets it; an administrator asking for
    # service accounts gets those too, and nobody else does.
    "api/routes/entity.py::get_users": "admin widens the directory",
    # Every account joins the All team when it is created, service accounts
    # included, so these answer a member a shorter member list than an
    # administrator. An administrator also sees every team in the org; a member
    # sees, and can read, only the teams they belong to.
    "api/routes/entity.py::get_team": "admin widens the member list",
    "api/routes/entity.py::get_user_teams": "admin widens the member list",
    "api/routes/entity.py::get_team_users": "admin widens the member list",
    # A team's OWNER may change or delete it, admin or not; an administrator may
    # also change or delete any team in their org.
    "api/routes/entity.py::update_team": "owner or admin",
    "api/routes/entity.py::delete_team": "owner or admin",
}


# A toolset's tool-rules routes answer 404 with this when the edition has no organization tool
# rules (`edition_config.organization_toolset_tool_rules`); their admin check is never reached.
ORG_TOOL_RULES_OFF = "Organization tool approvals aren't available here"
TOOL_RULES_HANDLERS = frozenset({
    "api/routes/toolsets.py::get_toolset_tool_rules",
    "api/routes/toolsets.py::update_toolset_tool_rules",
})


@dataclass(frozen=True)
class SwitchedOff:
    """A part of the product a stack may run without. While it is off, its
    routes give this answer before any admin check, so the live suite skips
    them on that stack instead of failing."""

    path_prefix: str
    status: int
    words: str
    why: str

    def applies(self, route: AdminRoute, status: int, text: str) -> bool:
        return (
            route.path.startswith(self.path_prefix)
            and status == self.status
            and self.words.lower() in text.lower()
        )


SWITCHED_OFF: tuple[SwitchedOff, ...] = ()

# Rows a downstream build adds (helper/suite_extensions.py). Last, so the
# extension can build its rows with everything above.
_extensions = suite_extensions.modules("admin_route_table")
NODE_ADMIN_ROUTES = suite_extensions.add_rows(
    NODE_ADMIN_ROUTES, "NODE_ADMIN_ROUTES", _extensions, key=lambda row: row.key)
PYTHON_ADMIN_ROUTES = suite_extensions.add_rows(
    PYTHON_ADMIN_ROUTES, "PYTHON_ADMIN_ROUTES", _extensions, key=lambda row: row.key)
ADMIN_BODIES = suite_extensions.add_items(ADMIN_BODIES, "ADMIN_BODIES", _extensions)
PYTHON_CONDITIONAL_ADMIN = suite_extensions.add_items(
    PYTHON_CONDITIONAL_ADMIN, "PYTHON_CONDITIONAL_ADMIN", _extensions)
SWITCHED_OFF = suite_extensions.add_rows(
    SWITCHED_OFF, "SWITCHED_OFF", _extensions, key=lambda rule: rule.path_prefix)
