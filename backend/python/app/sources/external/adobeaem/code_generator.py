# ruff: noqa
"""
Adobe Experience Manager (AEM) DataSource Code Generator

Defines AEM API endpoint specifications and generates the DataSource
wrapper class (adobeaem.py) from them.

Endpoints:
  /api/assets.json, /api/assets/{path}.json, /content/dam.json,
  /libs/granite/security/search/authorizables.json,
  /bin/querybuilder.json, /crx/packmgr/service.jsp
"""

from __future__ import annotations

ENDPOINTS = [
    # Assets API
    {"method": "GET", "path": "/api/assets.json", "name": "list_assets", "section": "Assets API",
     "doc": "List assets via the Assets HTTP API",
     "extra_query": [("limit", "limit", "int | None", "Maximum number of assets to return", True),
                     ("start", "start", "int | None", "Offset for pagination", True),
                     ("orderby", "orderby", "str | None", "Field to order results by", True)]},
    {"method": "GET", "path": "/api/assets/{path}.json", "name": "get_asset", "section": "Assets API",
     "doc": "Get a specific asset by path", "path_params": ["path"],
     "path_transform": {"path": "lstrip('/')"}},
    # DAM Content
    {"method": "GET", "path": "/content/dam.json", "name": "get_dam_content", "section": "DAM Content",
     "doc": "Browse DAM content",
     "extra_query": [("limit", "limit", "int | None", "Maximum number of results to return", True),
                     ("start", "start", "int | None", "Offset for pagination", True)]},
    # Authorizables
    {"method": "GET", "path": "/libs/granite/security/search/authorizables.json",
     "name": "search_authorizables", "section": "Authorizables (Users/Groups)",
     "doc": "Search for authorizables (users/groups)",
     "query_params": [("query", "query", "str", "Search query string")]},
    # QueryBuilder
    {"method": "GET", "path": "/bin/querybuilder.json", "name": "query_builder", "section": "QueryBuilder",
     "doc": "Execute a QueryBuilder query",
     "extra_query": [("path", "path", "str | None", "Content path to search under", True),
                     ("type", "type", "str | None", 'Node type to filter (e.g., "dam:Asset", "cq:Page")', True),
                     ("p_limit", "p.limit", "int | None", "Maximum number of results", True),
                     ("orderby", "orderby", "str | None", 'Sort field (e.g., "@jcr:content/jcr:lastModified")', True),
                     ("fulltext", "fulltext", "str | None", "Full-text search query", True)]},
    # Package Manager
    {"method": "GET", "path": "/crx/packmgr/service.jsp", "name": "get_package_list", "section": "Package Manager",
     "doc": "List packages via CRX Package Manager",
     "fixed_query": {"cmd": "ls"}},
]


def _gen_method(ep: dict) -> str:
    """Generate a single async method from an endpoint spec."""
    name = ep["name"]
    method = ep["method"]
    path = ep["path"]
    doc = ep["doc"]
    path_params = ep.get("path_params", [])
    body_params = ep.get("body_params", [])
    query_params = ep.get("query_params", [])
    extra_query = ep.get("extra_query", [])
    fixed_query = ep.get("fixed_query", {})
    has_query = query_params or extra_query or fixed_query

    sig_parts = ["self"]
    for p in path_params:
        sig_parts.append(f"{p}: str")
    for qp in query_params:
        sig_parts.append(f"{qp[0]}: {qp[2]}")
    for bp in body_params:
        sig_parts.append(f"{bp[0]}: {bp[2]}")
    if extra_query:
        sig_parts.append("*")
        for eq in extra_query:
            sig_parts.append(f"{eq[0]}: {eq[2]} = None")

    sig = ",\n        ".join(sig_parts)

    doc_args = ""
    if path_params or query_params or body_params or extra_query:
        doc_args = "\n        Args:\n"
        for p in path_params:
            doc_args += f"            {p}: The {p.replace('_', ' ')}\n"
        for qp in query_params:
            doc_args += f"            {qp[0]}: {qp[3]}\n"
        for bp in body_params:
            doc_args += f"            {bp[0]}: {bp[3]}\n"
        for eq in extra_query:
            doc_args += f"            {eq[0]}: {eq[3]}\n"

    query_block = ""
    if has_query:
        lines = ["", "        query_params: dict[str, Any] = {}"]
        for k, v in fixed_query.items():
            lines.append(f"        query_params['{k}'] = '{v}'")
        for qp in query_params:
            lines.append(f"        query_params['{qp[1]}'] = {qp[0]}")
        for eq in extra_query:
            lines.append(f"        if {eq[0]} is not None:")
            if "int" in eq[2]:
                lines.append(f"            query_params['{eq[1]}'] = str({eq[0]})")
            else:
                lines.append(f"            query_params['{eq[1]}'] = {eq[0]}")
        query_block = "\n".join(lines) + "\n"

    path_transform = ep.get("path_transform", {})
    if path_params:
        if path_transform:
            transform_lines = []
            for p in path_params:
                if p in path_transform:
                    transform_lines.append(f"        clean_{p} = {p}.{path_transform[p]}")
            if transform_lines:
                url_line = "\n".join(transform_lines) + "\n"
                fmt_args = ", ".join(f"{p}=clean_{p}" if p in path_transform else f"{p}={p}" for p in path_params)
            else:
                fmt_args = ", ".join(f"{p}={p}" for p in path_params)
                url_line = ""
            url_line += f'        url = self.base_url + "{path}".format({fmt_args})'
        else:
            fmt_args = ", ".join(f"{p}={p}" for p in path_params)
            url_line = f'        url = self.base_url + "{path}".format({fmt_args})'
    else:
        url_line = f'        url = self.base_url + "{path}"'

    body_block = ""
    if body_params:
        lines = ["\n        body: dict[str, Any] = {"]
        for bp in body_params:
            lines.append(f'            "{bp[1]}": {bp[0]},')
        lines.append("        }")
        body_block = "\n".join(lines)

    req_extra = ""
    if has_query:
        req_extra += "\n                query=query_params,"
    if body_params:
        req_extra += "\n                body=body,"

    return f'''
    async def {name}(
        {sig},
    ) -> AdobeAEMResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            AdobeAEMResponse with operation result
        """
{query_block}
{url_line}

        try:
            request = HTTPRequest(
                method="{method}",
                url=url,
                headers={{"Content-Type": "application/json"}},{req_extra}
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AdobeAEMResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return AdobeAEMResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full Adobe AEM DataSource module code."""
    header = '''# ruff: noqa
"""
Adobe Experience Manager (AEM as Cloud Service) REST API DataSource - Auto-generated API wrapper

Generated from AEM Assets HTTP API and QueryBuilder API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.adobeaem.adobeaem import AdobeAEMClient, AdobeAEMResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class AdobeAEMDataSource:
    """Adobe AEM REST API DataSource

    Provides async wrapper methods for AEM REST API operations:
    - Assets management (list, get)
    - DAM content browsing
    - User/authorizable search
    - QueryBuilder queries
    - Package management

    The base URL is https://{instance}.adobeaemcloud.com.

    All methods return AdobeAEMResponse objects.
    """

    def __init__(self, client: AdobeAEMClient) -> None:
        """Initialize with AdobeAEMClient.

        Args:
            client: AdobeAEMClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'AdobeAEMDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> AdobeAEMClient:
        """Return the underlying AdobeAEMClient."""
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
