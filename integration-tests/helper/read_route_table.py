"""What the read-route inventory accepts that is not a plain authenticated GET.

Merged with ``read_route_table`` modules in ``suite_extensions`` directories
(``helper/suite_extensions.py``). Each row has a one-line reason.
"""

from __future__ import annotations

from helper import suite_extensions

# operationIds of GETs the spec marks ``security: []``: they need no token on purpose.
PUBLIC_READS: frozenset[str] = frozenset({
    "checkOrgExists",  # sign-up flow asks whether an org exists before anyone has a token
    "getApiDocsHealth",  # health probe
    "getApiDocsJson",  # the published spec
    "getApiDocsUi",  # the docs page
    "getApiDocsUiSubPath",  # the docs page's static assets
    "getDesktopFrontendUrl",  # the desktop app asks where the web app lives before sign-in
    "getHealth",  # health probe
    "getOrgHealth",  # health probe
    "getServiceAccountDelegationCapability",  # sign-in page asks which login options exist
    "getServicesHealth",  # health probe
    "getUserGroupsHealth",  # health probe
    "getUsersHealth",  # health probe
    "jwks",  # OAuth discovery: public keys
    "oauthAuthorizationServerMetadata",  # OAuth discovery
    "oauthProtectedResource",  # OAuth discovery
    "openidConfiguration",  # OpenID discovery
})

# "GET /api/v1/x/{}" -> why the router serves it but the spec does not document it.
_INTERNAL = "internal service route, called with a service token, not part of the public API"
_CM = "/api/v1/configurationManager/internal"
UNDOCUMENTED_READS: dict[str, str] = {
    "GET /api/v1/docs/*": "static assets of the docs page",
    "GET /api/v1/document/internal/artifacts/{}/files/*": _INTERNAL,
    "GET /api/v1/userAccount/internal/password/check": _INTERNAL,
    "GET /api/v1/users/internal/admin-users": _INTERNAL,
    "GET /api/v1/users/internal/{}": _INTERNAL,
    "GET /api/v1/users/internal/{}/adminCheck": _INTERNAL,
    **{f"GET {_CM}/{tail}": _INTERNAL for tail in (
        "aiModelsConfig",
        "authConfig/google",
        "authConfig/microsoft",
        "connectors/atlassian/config",
        "connectors/business/googleWorkspaceCredentials",
        "connectors/googleWorkspaceOauthConfig",
        "connectors/individual/googleWorkspaceCredentials",
        "connectors/{}/config",
        "smtpConfig",
        "storageConfig",
    )},
}

# Router file (relative to the repo) -> why its mount prefix is not read from the source.
UNREAD_ROUTERS: dict[str, str] = {
    "backend/nodejs/apps/src/modules/configuration_manager/routes/openai_models.routes.ts":
        "one router, mounted twice (at /v1 and /api/v1), so no single prefix is read",
    "backend/nodejs/apps/src/modules/enterprise_search/routes/es.routes.ts":
        "five routers from one file, mounted at /api/v1/conversations, /api/v1/agents, /api/v1/chat, /api/v1/search and /api/v1/retrieve",
}

# operationId -> (statuses a request with no valid token may get, reason).
STATUS_EXCEPTIONS: dict[str, tuple[tuple[int, ...], str]] = {}

_extensions = suite_extensions.modules("read_route_table")
PUBLIC_READS = frozenset(PUBLIC_READS | {i for m in _extensions for i in getattr(m, "PUBLIC_READS", ())})
UNDOCUMENTED_READS = suite_extensions.add_items(UNDOCUMENTED_READS, "UNDOCUMENTED_READS", _extensions)
UNREAD_ROUTERS = suite_extensions.add_items(UNREAD_ROUTERS, "UNREAD_ROUTERS", _extensions)
STATUS_EXCEPTIONS = suite_extensions.add_items(STATUS_EXCEPTIONS, "STATUS_EXCEPTIONS", _extensions)
