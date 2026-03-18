# ruff: noqa
"""
Keycloak DataSource Code Generator

Defines Keycloak Admin REST API endpoint specifications and generates the
DataSource wrapper class (keycloak.py) from them.

Endpoints:
  /users, /users/{id}, /users/count, /groups, /groups/{id},
  /groups/{id}/members, /clients, /clients/{id}, /roles, /roles/{roleName},
  /roles/{roleName}/users, /events, /admin-events,
  /identity-provider/instances, /authentication/flows

Note: For OAuth clients, ensure_authenticated() is called if available.
"""

from __future__ import annotations

ENDPOINTS = [
    # Users
    {"method": "GET", "path": "/users", "name": "list_users", "section": "Users",
     "doc": "List all users in the realm",
     "query_params": [("first", "int", "Pagination offset"), ("max", "int", "Maximum results to return"),
                      ("search", "str", "Search string for username, first/last name, or email"),
                      ("username", "str", "Filter by username"), ("email", "str", "Filter by email"),
                      ("enabled", "str", "Filter by enabled status (true/false)")]},
    {"method": "GET", "path": "/users/{user_id}", "name": "get_user", "section": "Users",
     "doc": "Get a specific user by ID", "path_params": ["user_id"]},
    {"method": "GET", "path": "/users/count", "name": "get_users_count", "section": "Users",
     "doc": "Get the total number of users in the realm"},
    # Groups
    {"method": "GET", "path": "/groups", "name": "list_groups", "section": "Groups",
     "doc": "List all groups in the realm",
     "query_params": [("first", "int", "Pagination offset"), ("max", "int", "Maximum results to return"),
                      ("search", "str", "Search string for group name")]},
    {"method": "GET", "path": "/groups/{group_id}", "name": "get_group", "section": "Groups",
     "doc": "Get a specific group by ID", "path_params": ["group_id"]},
    {"method": "GET", "path": "/groups/{group_id}/members", "name": "get_group_members", "section": "Groups",
     "doc": "Get members of a specific group", "path_params": ["group_id"],
     "query_params": [("first", "int", "Pagination offset"), ("max", "int", "Maximum results to return")]},
    # Clients
    {"method": "GET", "path": "/clients", "name": "list_clients", "section": "Clients",
     "doc": "List all clients in the realm",
     "query_params": [("first", "int", "Pagination offset"), ("max", "int", "Maximum results to return"),
                      ("search", "str", "Filter by client ID or name")]},
    {"method": "GET", "path": "/clients/{client_id}", "name": "get_client", "section": "Clients",
     "doc": "Get a specific client by ID", "path_params": ["client_id"]},
    # Roles
    {"method": "GET", "path": "/roles", "name": "list_roles", "section": "Roles",
     "doc": "List all realm-level roles",
     "query_params": [("first", "int", "Pagination offset"), ("max", "int", "Maximum results to return"),
                      ("search", "str", "Filter by role name")]},
    {"method": "GET", "path": "/roles/{role_name}", "name": "get_role", "section": "Roles",
     "doc": "Get a specific role by name", "path_params": ["role_name"]},
    {"method": "GET", "path": "/roles/{role_name}/users", "name": "get_role_users", "section": "Roles",
     "doc": "Get users assigned to a specific role", "path_params": ["role_name"],
     "query_params": [("first", "int", "Pagination offset"), ("max", "int", "Maximum results to return")]},
    # Events
    {"method": "GET", "path": "/events", "name": "list_events", "section": "Events",
     "doc": "List login events in the realm",
     "query_params": [("type", "str", "Event type filter"), ("dateFrom", "str", "Date range start (yyyy-MM-dd)"),
                      ("dateTo", "str", "Date range end (yyyy-MM-dd)"),
                      ("first", "int", "Pagination offset"), ("max", "int", "Maximum results to return")]},
    {"method": "GET", "path": "/admin-events", "name": "list_admin_events", "section": "Events",
     "doc": "List admin events in the realm",
     "query_params": [("first", "int", "Pagination offset"), ("max", "int", "Maximum results to return")]},
    # Identity Providers
    {"method": "GET", "path": "/identity-provider/instances", "name": "list_identity_providers",
     "section": "Identity Providers", "doc": "List all identity provider instances"},
    # Authentication Flows
    {"method": "GET", "path": "/authentication/flows", "name": "list_authentication_flows",
     "section": "Authentication Flows", "doc": "List all authentication flows"},
]


def _gen_method(ep: dict) -> str:
    """Generate a single async method from an endpoint spec."""
    name = ep["name"]
    method = ep["method"]
    path = ep["path"]
    doc = ep["doc"]
    path_params = ep.get("path_params", [])
    query_params = ep.get("query_params", [])
    body_params = ep.get("body_params", [])

    sig_parts = ["self"]
    for p in path_params:
        sig_parts.append(f"{p}: str")
    for bp in body_params:
        sig_parts.append(f"{bp[0]}: {bp[2]}")
    if query_params:
        sig_parts.append("*")
        for qp in query_params:
            sig_parts.append(f"{qp[0]}: {qp[1]} | None = None")

    sig = ",\n        ".join(sig_parts)

    doc_args = ""
    if path_params or query_params or body_params:
        doc_args = "\n        Args:\n"
        for p in path_params:
            doc_args += f"            {p}: The {p.replace('_', ' ')}\n"
        for bp in body_params:
            doc_args += f"            {bp[0]}: {bp[3]}\n"
        for qp in query_params:
            doc_args += f"            {qp[0]}: {qp[2]}\n"

    query_block = ""
    if query_params:
        lines = ["\n        query_params: dict[str, Any] = {}"]
        for qp in query_params:
            lines.append(f"        if {qp[0]} is not None:")
            lines.append(f"            query_params['{qp[0]}'] = str({qp[0]})")
        query_block = "\n".join(lines) + "\n"

    if path_params:
        fmt_args = ", ".join(f"{p}={p}" for p in path_params)
        url_line = f'        url = self.base_url + "{path}".format({fmt_args})'
    else:
        url_line = f'        url = self.base_url + "{path}"'

    body_block = ""
    if body_params:
        lines = ["\n        body: dict[str, Any] = {}"]
        for bp in body_params:
            lines.append(f'        if {bp[0]} is not None:')
            lines.append(f'            body["{bp[1]}"] = {bp[0]}')
        body_block = "\n".join(lines)

    req_extra = ""
    if query_params:
        req_extra += "\n                query=query_params,"
    if body_params:
        req_extra += "\n                body=body,"

    return f'''
    async def {name}(
        {sig}
    ) -> KeycloakResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()
{query_block}
{url_line}
{body_block}

        try:
            request = HTTPRequest(
                method="{method}",
                url=url,
                headers={{"Content-Type": "application/json"}},{req_extra}
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full Keycloak DataSource module code."""
    header = '''# ruff: noqa
"""
Keycloak Admin REST API DataSource - Auto-generated API wrapper

Generated from Keycloak Admin REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.

Note: For OAuth clients, ensure_authenticated() is called before each
      request to auto-fetch a client_credentials OAuth token.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.keycloak.keycloak import KeycloakClient, KeycloakResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class KeycloakDataSource:
    """Keycloak Admin REST API DataSource

    Provides async wrapper methods for Keycloak Admin REST API operations:
    - Users management
    - Groups management
    - Clients management
    - Roles management
    - Events (login and admin)
    - Identity Providers
    - Authentication Flows

    All methods return KeycloakResponse objects.
    """

    def __init__(self, client: KeycloakClient) -> None:
        """Initialize with KeycloakClient.

        Args:
            client: KeycloakClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'KeycloakDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> KeycloakClient:
        """Return the underlying KeycloakClient."""
        return self._client
'''

    methods = []
    current_section = None
    for ep in ENDPOINTS:
        section = ep.get("section", "")
        if section and section != current_section:
            current_section = section
            methods.append(f"\n    # {'-' * 71}\n    # {section}\n    # {'-' * 71}")
        methods.append(_gen_method(ep))

    return header + "\n".join(methods) + "\n"


if __name__ == "__main__":
    print(generate_datasource())
