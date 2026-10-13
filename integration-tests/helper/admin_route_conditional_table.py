"""Live cases for the routes whose admin check depends on what is asked for.

``helper/admin_route_table.py`` lists these handlers in
``PYTHON_CONDITIONAL_ADMIN`` with the rule each follows. A member is not simply
refused on them, so one "member is refused" request cannot test them. Each
handler gets a row here instead: the route as the web app calls it, and one
case per (thing asked for, caller) pair that the rule tells apart.

``permissions/test_conditional_admin_routes.py`` sends every case to the
running stack. ``unit/test_admin_route_inventory.py`` fails until every handler
in ``PYTHON_CONDITIONAL_ADMIN`` has a row, so a new conditional route cannot be
added without live cases.

How a case reads:

* ``target`` names what the request is aimed at. It is a key of the test
  world (``WORLD_GROUPS`` below): a connector, a record, a skill.
* ``caller`` is the admin, the member who created the target, another member,
  or a service token.
* ``allowed`` says which side of the rule the caller is on. A refused caller
  gets the pinned refusal, or a 200 that leaves out what they may not see. An
  allowed caller gets past the check; where passing it for real would change
  something, the request is shaped so a later validator stops it, and that
  validator's answer is what is pinned.
* ``words`` must be in the reply, ``absent`` must not be.

A refusal on these routes is often a 404, the same answer a missing id gets, or
a list that simply lacks the thing. Each such case is paired with an allowed
case on the same target: that one proves the target exists and the route
works, which is what makes the 404 mean "not for you".

Placeholders in a path, query, body, ``words`` or ``absent`` are written
``{key}``. ``{target}`` is the case's own target, ``{target_name}`` its name and
``{target_state}`` the OAuth ``state`` value that carries its id.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

ADMIN = "admin"
# The member who created the target.
CREATOR = "creator"
MEMBER = "member"
# Service tokens, signed with the stack's scoped secret as the services sign them.
INDEXING_SERVICE = "indexing-service"
SERVICE_FOR_ADMIN = "service-for-admin"
SERVICE_FOR_MEMBER = "service-for-member"
CALLERS = frozenset({ADMIN, CREATOR, MEMBER, INDEXING_SERVICE, SERVICE_FOR_ADMIN, SERVICE_FOR_MEMBER})

# Where a route is served. The web app only ever calls the gateway; the
# connector service's own routes are reached the way its sibling services do.
GATEWAY = "gateway"
CONNECTOR_SERVICE = "connector-service"

# What the test world holds, by the group that is created together.
WORLD_GROUPS: dict[str, tuple[str, ...]] = {
    # The org every caller belongs to.
    "identity": ("org",),
    # A team connector the admin created and a personal one a member created.
    # Neither has ever synced.
    "connectors": ("team", "personal"),
    # The same pair with agents switched on, so every listing route returns them.
    "listed": ("listed_team", "listed_team_name", "listed_personal", "listed_personal_name"),
    # A sign-in app (OAuth client) for a connector type, with a known id and secret.
    "oauth": ("oauth_config", "oauth_name", "oauth_client_id", "oauth_secret"),
    # A toolset with a stored API key, and one with its own sign-in app.
    "toolsets": (
        "toolset", "toolset_name", "toolset_key",
        "toolset_oauth_name", "toolset_client_id", "toolset_secret",
    ),
    # A note in the admin's private knowledge base, and one in a member's.
    "records": ("admin_record", "admin_record_text", "member_record", "member_record_text"),
    # A download link for the admin's note, made out to the admin.
    "signed_link": ("signed_token",),
    # A built-in skill, and a member's own skills: one switched on, one off.
    "skills": ("builtin_skill", "custom_skill", "disabled_skill"),
    # MCP servers: one every user shares the admin's credential on, one each
    # user signs in to themselves.
    "mcp": ("shared_mcp", "own_mcp"),
}
# Made new each time they are asked for.
FRESH_KEYS = frozenset({"fresh_team", "fresh_personal", "unique_name"})
DERIVED_KEYS = frozenset({"target", "target_name", "target_state"})
WORLD_KEYS = frozenset(key for keys in WORLD_GROUPS.values() for key in keys)

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class Case:
    target: str
    caller: str
    allowed: bool
    status: int
    words: str = ""
    absent: tuple[str, ...] = ()
    # Replace the route's own query or body for this case.
    params: dict[str, str] | None = None
    body: Any = None

    @property
    def id(self) -> str:
        return f"{self.target}/{self.caller}"


@dataclass(frozen=True)
class ConditionalRoute:
    handler: str
    method: str
    path: str
    cases: tuple[Case, ...]
    params: dict[str, str] = field(default_factory=dict)
    body: Any = None
    service: str = GATEWAY
    # Filled in when the handler asks who is calling but answers everyone the
    # same way. Such a route needs a member case and an admin case that match.
    same_for_everyone: str = ""

    @property
    def id(self) -> str:
        return f"{self.method} {self.path}"


def placeholders(value: Any) -> set[str]:
    """Every ``{key}`` in a string, or anywhere inside a dict, list or tuple."""
    if isinstance(value, str):
        return set(_PLACEHOLDER.findall(value))
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (list, tuple)):
        return set().union(*(placeholders(item) for item in value))
    return set()


def fill(value: Any, lookup: Callable[[str], str]) -> Any:
    """``value`` with each ``{key}`` replaced by ``lookup(key)``."""
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda match: lookup(match.group(1)), value)
    if isinstance(value, dict):
        return {key: fill(item, lookup) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(fill(item, lookup) for item in value)
    return value


# not_found("This connector") in app/utils/user_messages.py: the registry
# answers the same way for a connector that is not there and one that is not yours.
NOT_YOURS = "This connector was removed, or you no longer have access"
_CONNECTORS = "/api/v1/connectors"
_ROUTER = "connectors/api/router.py"


def _on_a_connector(
    name: str,
    method: str,
    suffix: str,
    status: int,
    words: str = "",
    *,
    body: Any = None,
    params: dict[str, str] | None = None,
    refusal: tuple[int, str] = (404, NOT_YOURS),
) -> ConditionalRoute:
    """A route aimed at one connector: open to the admin on a team connector and
    to the creator on a personal one, and to nobody else, the admin included."""
    refused_status, refused_words = refusal

    def case(target: str, caller: str, *, allowed: bool) -> Case:
        if allowed:
            return Case(target, caller, True, status, words, absent=(refused_words,))
        return Case(target, caller, False, refused_status, refused_words)

    return ConditionalRoute(
        f"{_ROUTER}::{name}", method, f"{_CONNECTORS}{suffix}",
        cases=(
            case("team", MEMBER, allowed=False),
            case("team", ADMIN, allowed=True),
            case("personal", CREATOR, allowed=True),
            case("personal", MEMBER, allowed=False),
            case("personal", ADMIN, allowed=False),
        ),
        params=params or {}, body=body,
    )


def _listing(name: str, suffix: str) -> ConditionalRoute:
    """A connector list: an unshared team connector is shown to the admin only,
    a personal one to its creator only."""

    def case(target: str, caller: str, shown: bool) -> Case:
        scope = "team" if target == "listed_team" else "personal"
        return Case(
            target, caller, shown, 200,
            words="{target}" if shown else "",
            absent=() if shown else ("{target}",),
            params={"scope": scope, "search": "{target_name}", "limit": "50"},
        )

    return ConditionalRoute(
        f"{_ROUTER}::{name}", "GET", f"{_CONNECTORS}{suffix}",
        cases=(
            case("listed_team", ADMIN, True),
            case("listed_team", MEMBER, False),
            case("listed_personal", CREATOR, True),
            case("listed_personal", MEMBER, False),
            case("listed_personal", ADMIN, False),
        ),
    )


# A connector type that needs no account anywhere to create.
PLAIN_CONNECTOR_TYPE = "Web"
# One that agents can use, for the list of connectors agents may use. It is
# created without credentials and never synced.
AGENT_CONNECTOR_TYPE = "S3"
AGENT_CONNECTOR_AUTH = "ACCESS_KEY"
# Connector and toolset types no other suite makes sign-in apps for.
OAUTH_CONNECTOR_TYPE = "Zoom"
KEYED_TOOLSET_TYPE = "lumos"
KEYED_TOOLSET_FIELD = "apiKey"
OAUTH_TOOLSET_TYPE = "clickup"

# The create route checks the scope before the sign-in type, so this request is
# refused by scope alone, and past that check nothing is created.
_NO_SUCH_AUTH = "IT_NO_SUCH_AUTH"
_UNSUPPORTED_AUTH = f"Auth type '{_NO_SUCH_AUTH}' is not supported"


def _create_body(scope: str) -> dict[str, str]:
    return {
        "connectorType": PLAIN_CONNECTOR_TYPE, "instanceName": "{unique_name}",
        "scope": scope, "authType": _NO_SUCH_AUTH,
    }


_MALFORMED_FILTERS = {"filters": {"sync": "it-not-an-object"}}
_FILTERS_REFUSED = "filters.sync must be an object"
_DELETING = "Connector deletion initiated"
_NO_RECORD_ACCESS = "You do not have permission to access this record"
_SERVICE_TOKEN_ONLY = "This route requires a service token"
_ADMIN_ONLY_SKILL = "only an organization admin can change its availability"
_WRONG_SKILL_STATE = "cannot transition"
_SHARED_CREDENTIAL = "shared admin credential; only administrators can manage it"
_TOKEN_REQUIRED = "apiToken is required for this instance"
_SKILLS = "/api/v1/skills"
_MCP = "/api/v1/mcp-servers/instances"
_TOOLSETS = "/api/v1/toolsets"
_OAUTH = f"/api/v1/oauth/{OAUTH_CONNECTOR_TYPE}"


def _secrets(name: str, module: str, path: str, shown: str, hidden: tuple[str, ...], *,
             admin_sees: str, admin_hidden: tuple[str, ...] = (),
             params: dict[str, str] | None = None) -> ConditionalRoute:
    """A read that every member may make, with the secrets left out for them."""
    return ConditionalRoute(
        f"{module}::{name}", "GET", path,
        cases=(
            Case("secrets", MEMBER, False, 200, words=shown, absent=hidden),
            Case("secrets", ADMIN, True, 200, words=admin_sees, absent=admin_hidden),
        ),
        params=params or {},
    )


def _mcp(name: str, method: str, suffix: str, status: int, words: str) -> ConditionalRoute:
    refused = (403, _SHARED_CREDENTIAL)
    return ConditionalRoute(
        f"api/routes/mcp_servers.py::{name}", method, f"{_MCP}/{{target}}{suffix}",
        cases=(
            Case("shared_mcp", MEMBER, False, *refused),
            Case("shared_mcp", ADMIN, True, status, words),
            Case("own_mcp", MEMBER, True, status, words),
        ),
        body=None if method == "DELETE" else {},
    )


CONDITIONAL_ROUTES: tuple[ConditionalRoute, ...] = (
    # The test world creates the admin's team connector and the member's
    # personal one for real, which is the full proof that they may. Here the
    # allowed callers only show that they get past the scope check.
    ConditionalRoute(
        f"{_ROUTER}::create_connector_instance", "POST", f"{_CONNECTORS}/",
        cases=(
            Case("team", MEMBER, False, 403, "Only administrators can create team connectors",
                 body=_create_body("team")),
            Case("team", ADMIN, True, 400, _UNSUPPORTED_AUTH, body=_create_body("team")),
            Case("personal", MEMBER, True, 400, _UNSUPPORTED_AUTH, body=_create_body("personal")),
        ),
    ),
    # Deleting is the one thing an admin may do to someone else's personal
    # connector. Each case gets a connector of its own to delete.
    ConditionalRoute(
        f"{_ROUTER}::delete_connector_instance", "DELETE", f"{_CONNECTORS}/{{target}}",
        cases=(
            Case("fresh_team", MEMBER, False, 404, NOT_YOURS),
            Case("fresh_team", ADMIN, True, 202, _DELETING),
            Case("fresh_personal", CREATOR, True, 202, _DELETING),
            Case("fresh_personal", MEMBER, False, 404, NOT_YOURS),
            Case("fresh_personal", ADMIN, True, 202, _DELETING),
        ),
    ),
    _on_a_connector("get_connector_instance", "GET", "/{target}", 200),
    _on_a_connector("get_connector_instance_config", "GET", "/{target}/config", 200),
    # The plain connector has no sign-in, which this route cannot list filters for.
    _on_a_connector("get_connector_instance_filters", "GET", "/{target}/filters", 400,
                    "Unsupported authentication type"),
    _on_a_connector("get_filter_field_options", "GET",
                    "/{target}/filters/it-no-such-filter/options", 404,
                    "Filter field 'it-no-such-filter' not found"),
    _on_a_connector("save_connector_instance_filters", "POST", "/{target}/filters", 200,
                    "Filter selections saved",
                    body={"filters": {"it_probe": {"operator": "in", "value": ["x"]}}}),
    # Asked to switch agents on, which the plain connector does not support.
    # Asked to switch sync on, the gateway answers a refused caller 500 before
    # the connector service is asked, and an allowed caller would start a crawl.
    _on_a_connector("toggle_connector_instance", "POST", "/{target}/toggle", 400,
                    "does not support agent functionality", body={"type": "agent"}),
    _on_a_connector("update_connector_instance_auth_config", "PUT", "/{target}/config/auth", 200,
                    "Authentication configuration saved", body={"auth": {}}),
    # The connector being edited goes in the body. The test world's connectors are
    # not PostgreSQL, so a caller allowed to use one is stopped by the type check
    # that follows, before any connection is tried.
    _on_a_connector("check_connector_connection", "POST", "/registry/PostgreSQL/test-connection", 400,
                    "connectorId is not a connector of this type",
                    body={"auth": {}, "connectorId": "{target}"}),
    _on_a_connector("update_connector_instance_config", "PUT", "/{target}/config", 400,
                    _FILTERS_REFUSED, body=_MALFORMED_FILTERS),
    _on_a_connector("update_connector_instance_filters_sync_config", "PUT",
                    "/{target}/config/filters-sync", 400, _FILTERS_REFUSED, body=_MALFORMED_FILTERS),
    _on_a_connector("update_connector_instance_name", "PUT", "/{target}/name", 200,
                    body={"instanceName": "{unique_name}"}),
    _on_a_connector("get_oauth_authorization_url", "GET", "/{target}/oauth/authorize", 400,
                    "does not support OAuth"),
    # The connector's id travels in ``state``. The handler answers 200 with an
    # error name either way, so the name is what tells the two apart.
    _on_a_connector("handle_oauth_callback", "GET", "/oauth/callback", 200, '"success":false',
                    params={"code": "it-code", "state": "{target_state}"},
                    refusal=(200, "instance_not_found")),
    _on_a_connector("reindex_connector", "POST", "/{target}/reindex", 409, "currently disabled",
                    body={}),
    _on_a_connector("stop_connector_sync", "POST", "/{target}/sync/stop", 200,
                    "No sync is currently running", body={}),
    _listing("get_connector_instances", "/"),
    _listing("get_configured_connector_instances", "/configured"),
    _listing("get_active_agent_instances", "/agents/active"),
    ConditionalRoute(
        f"{_ROUTER}::get_connector_registry", "GET", f"{_CONNECTORS}/registry",
        cases=(
            Case("types", MEMBER, True, 200, f'"type":"{PLAIN_CONNECTOR_TYPE}"'),
            Case("types", ADMIN, True, 200, f'"type":"{PLAIN_CONNECTOR_TYPE}"'),
        ),
        params={"search": PLAIN_CONNECTOR_TYPE, "limit": "50"},
        same_for_everyone="the admin flag is passed to the registry, which does not use it",
    ),
    ConditionalRoute(
        f"{_ROUTER}::get_all_oauth_configs", "GET", "/api/v1/oauth",
        cases=(
            Case("secrets", MEMBER, True, 200, "{oauth_name}",
                 absent=("{oauth_client_id}", "{oauth_secret}")),
            Case("secrets", ADMIN, True, 200, "{oauth_name}",
                 absent=("{oauth_client_id}", "{oauth_secret}")),
        ),
        params={"search": "{oauth_name}"},
        same_for_everyone="this list carries names only; the per-type routes hold the secrets",
    ),
    _secrets("list_oauth_configs", _ROUTER, _OAUTH, "{oauth_name}",
             ("{oauth_client_id}", "{oauth_secret}"), admin_sees="{oauth_client_id}",
             params={"search": "{oauth_name}"}),
    _secrets("get_oauth_config_by_id", _ROUTER, f"{_OAUTH}/{{oauth_config}}", "{oauth_name}",
             ("{oauth_client_id}", "{oauth_secret}"), admin_sees="{oauth_client_id}"),
    # ``"auth":`` is the stored credentials; ``"authType":`` is in every reply.
    _secrets("get_toolset_instances", "api/routes/toolsets.py", f"{_TOOLSETS}/instances",
             "{toolset_name}", ('"auth":', "{toolset_key}"), admin_sees='"auth":',
             params={"search": "{toolset_name}"}),
    _secrets("get_toolset_instance", "api/routes/toolsets.py", f"{_TOOLSETS}/instances/{{toolset}}",
             "{toolset_name}", ('"auth":', "{toolset_key}"), admin_sees='"auth":'),
    # The admin is shown the client id; the secret comes back masked even for them.
    _secrets("list_toolset_oauth_configs", "api/routes/toolsets.py",
             f"{_TOOLSETS}/oauth-configs/{OAUTH_TOOLSET_TYPE}", "{toolset_oauth_name}",
             ("{toolset_client_id}", "{toolset_secret}"), admin_sees="{toolset_client_id}",
             admin_hidden=("{toolset_secret}",)),
    # Being an admin opens no record: the admin is refused a member's private
    # note exactly as a member is refused the admin's.
    ConditionalRoute(
        f"{_ROUTER}::stream_record", "GET", "/api/v1/knowledgeBase/stream/record/{target}",
        cases=(
            Case("admin_record", MEMBER, False, 403, _NO_RECORD_ACCESS),
            Case("admin_record", ADMIN, True, 200, "{admin_record_text}"),
            Case("member_record", CREATOR, True, 200, "{member_record_text}"),
            Case("member_record", ADMIN, False, 403, _NO_RECORD_ACCESS),
        ),
    ),
    # A download link works only for the person it was made out to.
    ConditionalRoute(
        f"{_ROUTER}::download_file", "GET", "/api/v1/index/{org}/knowledgebase/record/{target}",
        cases=(
            Case("admin_record", ADMIN, True, 200, "{admin_record_text}"),
            Case("admin_record", MEMBER, False, 404, "Record not found"),
        ),
        params={"token": "{signed_token}"}, service=CONNECTOR_SERVICE,
    ),
    # The indexing service's read. No signed-in person may use it, admin or not.
    ConditionalRoute(
        f"{_ROUTER}::stream_record_internal", "GET", "/api/v1/internal/stream/record/{target}/",
        cases=(
            Case("admin_record", ADMIN, False, 403, _SERVICE_TOKEN_ONLY),
            Case("admin_record", MEMBER, False, 403, _SERVICE_TOKEN_ONLY),
            Case("admin_record", INDEXING_SERVICE, True, 200, "{admin_record_text}"),
        ),
        service=CONNECTOR_SERVICE,
    ),
    # The agent's read. A service token names the person it acts for, and that
    # person's access to the record is what decides.
    ConditionalRoute(
        f"{_ROUTER}::get_record_content_internal", "GET",
        "/api/v1/internal/records/{target}/content",
        cases=(
            Case("admin_record", ADMIN, False, 403, _SERVICE_TOKEN_ONLY),
            Case("admin_record", MEMBER, False, 403, _SERVICE_TOKEN_ONLY),
            Case("admin_record", SERVICE_FOR_ADMIN, True, 200, "{admin_record_text}"),
            Case("admin_record", SERVICE_FOR_MEMBER, False, 403, _NO_RECORD_ACCESS),
        ),
        service=CONNECTOR_SERVICE,
    ),
    # A member's own skill is theirs alone to switch, and nobody else can find
    # it. The allowed callers ask for the state the skill is already in, which
    # is refused after the check and changes nothing. The admin is not asked to
    # switch a built-in skill off: that would mute it for the whole org.
    ConditionalRoute(
        "api/routes/skills.py::disable_skill", "POST", f"{_SKILLS}/{{target}}/disable",
        cases=(
            Case("builtin_skill", MEMBER, False, 403, _ADMIN_ONLY_SKILL),
            Case("disabled_skill", CREATOR, True, 409, _WRONG_SKILL_STATE),
            Case("disabled_skill", MEMBER, False, 404, "not found"),
            Case("disabled_skill", ADMIN, False, 404, "not found"),
        ),
        body={},
    ),
    ConditionalRoute(
        "api/routes/skills.py::enable_skill", "POST", f"{_SKILLS}/{{target}}/enable",
        cases=(
            Case("builtin_skill", MEMBER, False, 403, _ADMIN_ONLY_SKILL),
            Case("builtin_skill", ADMIN, True, 409, _WRONG_SKILL_STATE),
            Case("custom_skill", CREATOR, True, 409, _WRONG_SKILL_STATE),
            Case("custom_skill", MEMBER, False, 404, "not found"),
            Case("custom_skill", ADMIN, False, 404, "not found"),
        ),
        body={},
    ),
    # No token is sent, so past the check the request is refused for lacking one.
    _mcp("authenticate_instance", "POST", "/authenticate", 400, _TOKEN_REQUIRED),
    _mcp("update_credentials", "PUT", "/credentials", 400, _TOKEN_REQUIRED),
    # Removes the caller's own stored credential, of which there is none.
    _mcp("remove_credentials", "DELETE", "/credentials", 200, '"success":true'),
)

# The (target, caller, allowed) cases a connector rule must cover, by the rule's
# name in PYTHON_CONDITIONAL_ADMIN. A target's ``fresh_`` or ``listed_`` prefix
# is left off: it says how the connector is made, not whose it is.
REQUIRED_CASES: dict[str, frozenset[tuple[str, str, bool]]] = {
    "team connectors need admin": frozenset({
        ("team", MEMBER, False), ("team", ADMIN, True),
        ("personal", CREATOR, True), ("personal", MEMBER, False), ("personal", ADMIN, False),
    }),
    "creator or admin": frozenset({
        ("team", MEMBER, False), ("team", ADMIN, True),
        ("personal", CREATOR, True), ("personal", MEMBER, False), ("personal", ADMIN, True),
    }),
    "team scope needs admin": frozenset({
        ("team", MEMBER, False), ("team", ADMIN, True), ("personal", MEMBER, True),
    }),
    "admin widens the listing": frozenset({
        ("team", ADMIN, True), ("team", MEMBER, False),
        ("personal", CREATOR, True), ("personal", MEMBER, False), ("personal", ADMIN, False),
    }),
}


def whose(target: str) -> str:
    return target.removeprefix("fresh_").removeprefix("listed_")
