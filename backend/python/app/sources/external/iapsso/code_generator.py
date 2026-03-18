# ruff: noqa
"""
IAP SSO (Google Cloud Identity-Aware Proxy) DataSource Code Generator

Defines IAP API endpoint specifications and generates the DataSource
wrapper class (iapsso.py) from them.

Endpoints:
  /{resource}:getIamPolicy (POST), /{resource}:setIamPolicy (POST),
  /{resource}:testIamPermissions (POST),
  /projects/{project}/iap_tunnel/locations/{location}/destGroups,
  /projects/{project}/iap_tunnel/locations/{location}/destGroups/{destGroupId},
  /projects/{project}/brands,
  /projects/{project}/brands/{brandId}/identityAwareProxyClients
"""

from __future__ import annotations

ENDPOINTS = [
    # IAM Policy
    {"method": "POST", "path": "/{resource}:getIamPolicy", "name": "get_iam_policy",
     "section": "IAM Policy",
     "doc": "Get the IAM policy for an IAP-protected resource",
     "path_params": ["resource"]},
    {"method": "POST", "path": "/{resource}:setIamPolicy", "name": "set_iam_policy",
     "section": "IAM Policy",
     "doc": "Set the IAM policy for an IAP-protected resource",
     "path_params": ["resource"],
     "body_params": [("policy", "policy", "dict[str, Any]", "The IAM policy to set")]},
    {"method": "POST", "path": "/{resource}:testIamPermissions", "name": "test_iam_permissions",
     "section": "IAM Policy",
     "doc": "Test IAM permissions for an IAP-protected resource",
     "path_params": ["resource"],
     "body_params": [("permissions", "permissions", "list[str]", "List of permissions to test")]},
    # Tunnel Dest Groups
    {"method": "GET", "path": "/projects/{project}/iap_tunnel/locations/{location}/destGroups",
     "name": "list_tunnel_dest_groups", "section": "Tunnel Dest Groups",
     "doc": "List tunnel destination groups", "path_params": ["project", "location"],
     "query_params": [("pageSize", "int", "Maximum number of results per page"),
                      ("pageToken", "str", "Token for pagination")]},
    {"method": "GET",
     "path": "/projects/{project}/iap_tunnel/locations/{location}/destGroups/{dest_group_id}",
     "name": "get_tunnel_dest_group", "section": "Tunnel Dest Groups",
     "doc": "Get a specific tunnel destination group",
     "path_params": ["project", "location", "dest_group_id"]},
    # Brands
    {"method": "GET", "path": "/projects/{project}/brands", "name": "list_brands",
     "section": "Brands",
     "doc": "List OAuth brands for a project", "path_params": ["project"]},
    # Identity-Aware Proxy Clients
    {"method": "GET", "path": "/projects/{project}/brands/{brand_id}/identityAwareProxyClients",
     "name": "list_iap_clients", "section": "IAP Clients",
     "doc": "List Identity-Aware Proxy clients for a brand",
     "path_params": ["project", "brand_id"],
     "query_params": [("pageSize", "int", "Maximum number of results per page"),
                      ("pageToken", "str", "Token for pagination")]},
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
    ) -> IAPSSOResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            IAPSSOResponse with operation result
        """
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
            return IAPSSOResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return IAPSSOResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full IAP SSO DataSource module code."""
    header = '''# ruff: noqa
"""
IAP SSO (Google Cloud Identity-Aware Proxy) DataSource - Auto-generated API wrapper

Generated from Google Cloud IAP API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.iapsso.iapsso import IAPSSOClient, IAPSSOResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class IAPSSODataSource:
    """IAP SSO (Google Cloud Identity-Aware Proxy) DataSource

    Provides async wrapper methods for Google Cloud IAP API operations:
    - IAM Policy management (get, set, test permissions)
    - Tunnel Destination Groups management
    - OAuth Brands management
    - Identity-Aware Proxy Clients management

    All methods return IAPSSOResponse objects.
    """

    def __init__(self, client: IAPSSOClient) -> None:
        """Initialize with IAPSSOClient.

        Args:
            client: IAPSSOClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'IAPSSODataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> IAPSSOClient:
        """Return the underlying IAPSSOClient."""
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
