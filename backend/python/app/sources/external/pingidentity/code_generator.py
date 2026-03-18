# ruff: noqa
"""
Ping Identity (PingOne) DataSource Code Generator

Defines PingOne API endpoint specifications and generates the DataSource
wrapper class (pingidentity.py) from them.

Endpoints:
  /users, /users/{userId}, /groups, /groups/{groupId},
  /populations, /populations/{populationId},
  /applications, /applications/{applicationId},
  /signOnPolicies, /signOnPolicies/{policyId},
  /schemas, /passwordPolicies, /identityProviders, /gateways

Note: For OAuth clients, ensure_authenticated() is called if available.
"""

from __future__ import annotations

ENDPOINTS = [
    # Users
    {"method": "GET", "path": "/users", "name": "list_users", "section": "Users",
     "doc": "List all users in the environment",
     "query_params": [("limit", "int", "Maximum number of results"), ("filter", "str", "SCIM filter expression")]},
    {"method": "GET", "path": "/users/{user_id}", "name": "get_user", "section": "Users",
     "doc": "Get a specific user by ID", "path_params": ["user_id"]},
    # Groups
    {"method": "GET", "path": "/groups", "name": "list_groups", "section": "Groups",
     "doc": "List all groups in the environment",
     "query_params": [("limit", "int", "Maximum number of results"), ("filter", "str", "SCIM filter expression")]},
    {"method": "GET", "path": "/groups/{group_id}", "name": "get_group", "section": "Groups",
     "doc": "Get a specific group by ID", "path_params": ["group_id"]},
    # Populations
    {"method": "GET", "path": "/populations", "name": "list_populations", "section": "Populations",
     "doc": "List all populations in the environment",
     "query_params": [("limit", "int", "Maximum number of results")]},
    {"method": "GET", "path": "/populations/{population_id}", "name": "get_population", "section": "Populations",
     "doc": "Get a specific population by ID", "path_params": ["population_id"]},
    # Applications
    {"method": "GET", "path": "/applications", "name": "list_applications", "section": "Applications",
     "doc": "List all applications in the environment",
     "query_params": [("limit", "int", "Maximum number of results")]},
    {"method": "GET", "path": "/applications/{application_id}", "name": "get_application", "section": "Applications",
     "doc": "Get a specific application by ID", "path_params": ["application_id"]},
    # Sign-On Policies
    {"method": "GET", "path": "/signOnPolicies", "name": "list_sign_on_policies", "section": "Sign-On Policies",
     "doc": "List all sign-on policies in the environment",
     "query_params": [("limit", "int", "Maximum number of results")]},
    {"method": "GET", "path": "/signOnPolicies/{policy_id}", "name": "get_sign_on_policy",
     "section": "Sign-On Policies",
     "doc": "Get a specific sign-on policy by ID", "path_params": ["policy_id"]},
    # Schemas
    {"method": "GET", "path": "/schemas", "name": "list_schemas", "section": "Schemas",
     "doc": "List all schemas in the environment"},
    # Password Policies
    {"method": "GET", "path": "/passwordPolicies", "name": "list_password_policies",
     "section": "Password Policies",
     "doc": "List all password policies in the environment"},
    # Identity Providers
    {"method": "GET", "path": "/identityProviders", "name": "list_identity_providers",
     "section": "Identity Providers",
     "doc": "List all identity providers in the environment"},
    # Gateways
    {"method": "GET", "path": "/gateways", "name": "list_gateways", "section": "Gateways",
     "doc": "List all gateways in the environment"},
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
    ) -> PingIdentityResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            PingIdentityResponse with operation result
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
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full PingIdentity DataSource module code."""
    header = '''# ruff: noqa
"""
Ping Identity (PingOne) REST API DataSource - Auto-generated API wrapper

Generated from PingOne Platform API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.

Note: For OAuth clients, ensure_authenticated() is called before each
      request to auto-fetch a client_credentials OAuth token.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.pingidentity.pingidentity import PingIdentityClient, PingIdentityResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class PingIdentityDataSource:
    """PingOne REST API DataSource

    Provides async wrapper methods for PingOne REST API operations:
    - Users management
    - Groups management
    - Populations management
    - Applications management
    - Sign-On Policies management
    - Schemas management
    - Password Policies management
    - Identity Providers management
    - Gateways management

    All methods return PingIdentityResponse objects.
    """

    def __init__(self, client: PingIdentityClient) -> None:
        """Initialize with PingIdentityClient.

        Args:
            client: PingIdentityClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'PingIdentityDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> PingIdentityClient:
        """Return the underlying PingIdentityClient."""
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
