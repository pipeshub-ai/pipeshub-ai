# ruff: noqa
"""
Simpplr DataSource Code Generator

Defines Simpplr API endpoint specifications and generates the DataSource
wrapper class (simpplr.py) from them.

Endpoints:
  /sites, /sites/{id}, /content, /content/{id}, /users, /users/{id},
  /pages, /pages/{id}, /events, /events/{id}, /newsletters, /newsletters/{id},
  /search (query: q, type, limit, offset), /analytics/content
"""

from __future__ import annotations

ENDPOINTS = [
    # Sites
    {"method": "GET", "path": "/sites", "name": "list_sites", "section": "Sites",
     "doc": "List all sites", "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/sites/{site_id}", "name": "get_site", "section": "Sites",
     "doc": "Get a specific site by ID", "path_params": ["site_id"]},
    # Content
    {"method": "GET", "path": "/content", "name": "list_content", "section": "Content",
     "doc": "List all content", "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/content/{content_id}", "name": "get_content", "section": "Content",
     "doc": "Get a specific content item by ID", "path_params": ["content_id"]},
    # Users
    {"method": "GET", "path": "/users", "name": "list_users", "section": "Users",
     "doc": "List all users", "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/users/{user_id}", "name": "get_user", "section": "Users",
     "doc": "Get a specific user by ID", "path_params": ["user_id"]},
    # Pages
    {"method": "GET", "path": "/pages", "name": "list_pages", "section": "Pages",
     "doc": "List all pages", "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/pages/{page_id}", "name": "get_page", "section": "Pages",
     "doc": "Get a specific page by ID", "path_params": ["page_id"]},
    # Events
    {"method": "GET", "path": "/events", "name": "list_events", "section": "Events",
     "doc": "List all events", "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/events/{event_id}", "name": "get_event", "section": "Events",
     "doc": "Get a specific event by ID", "path_params": ["event_id"]},
    # Newsletters
    {"method": "GET", "path": "/newsletters", "name": "list_newsletters", "section": "Newsletters",
     "doc": "List all newsletters", "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/newsletters/{newsletter_id}", "name": "get_newsletter", "section": "Newsletters",
     "doc": "Get a specific newsletter by ID", "path_params": ["newsletter_id"]},
    # Search
    {"method": "GET", "path": "/search", "name": "search", "section": "Search",
     "doc": "Search across Simpplr content",
     "query_params": [("q", "str", "Search query string"), ("type", "str", "Content type filter"), ("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    # Analytics
    {"method": "GET", "path": "/analytics/content", "name": "get_content_analytics", "section": "Analytics",
     "doc": "Get content analytics"},
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
        lines = ["\n        body: dict[str, Any] = {"]
        for bp in body_params:
            lines.append(f'            "{bp[1]}": {bp[0]},')
        lines.append("        }")
        body_block = "\n".join(lines)

    req_extra = ""
    if query_params:
        req_extra += "\n                query=query_params,"
    if body_params:
        req_extra += "\n                body=body,"

    return f'''
    async def {name}(
        {sig}
    ) -> SimpplrResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            SimpplrResponse with operation result
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
            return SimpplrResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return SimpplrResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full Simpplr DataSource module code."""
    header = '''# ruff: noqa
"""
Simpplr REST API DataSource - Auto-generated API wrapper

Generated from Simpplr REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.simpplr.simpplr import SimpplrClient, SimpplrResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class SimpplrDataSource:
    """Simpplr REST API DataSource

    Provides async wrapper methods for Simpplr REST API operations:
    - Sites management
    - Content management
    - Users management
    - Pages management
    - Events management
    - Newsletters management
    - Search
    - Analytics

    All methods return SimpplrResponse objects.
    """

    def __init__(self, client: SimpplrClient) -> None:
        """Initialize with SimpplrClient.

        Args:
            client: SimpplrClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'SimpplrDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> SimpplrClient:
        """Return the underlying SimpplrClient."""
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
