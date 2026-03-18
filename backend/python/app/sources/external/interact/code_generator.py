# ruff: noqa
"""
Interact (Interact Intranet) DataSource Code Generator

Defines Interact API endpoint specifications and generates the DataSource
wrapper class (interact.py) from them.

Endpoints:
  /users, /users/{id}, /content, /content/{id}, /pages, /pages/{id},
  /news, /news/{id}, /communities, /communities/{id}, /events, /events/{id},
  /search
"""

from __future__ import annotations

ENDPOINTS = [
    # Users
    {"method": "GET", "path": "/users", "name": "get_users", "section": "Users",
     "doc": "List all users", "paginated": True},
    {"method": "GET", "path": "/users/{user_id}", "name": "get_user", "section": "Users",
     "doc": "Get a specific user by ID", "path_params": ["user_id"]},
    # Content
    {"method": "GET", "path": "/content", "name": "get_content_list", "section": "Content",
     "doc": "List all content items", "paginated": True},
    {"method": "GET", "path": "/content/{content_id}", "name": "get_content", "section": "Content",
     "doc": "Get a specific content item by ID", "path_params": ["content_id"]},
    # Pages
    {"method": "GET", "path": "/pages", "name": "get_pages", "section": "Pages",
     "doc": "List all pages", "paginated": True},
    {"method": "GET", "path": "/pages/{page_id}", "name": "get_page", "section": "Pages",
     "doc": "Get a specific page by ID", "path_params": ["page_id"]},
    # News
    {"method": "GET", "path": "/news", "name": "get_news_list", "section": "News",
     "doc": "List all news items", "paginated": True},
    {"method": "GET", "path": "/news/{news_id}", "name": "get_news", "section": "News",
     "doc": "Get a specific news item by ID", "path_params": ["news_id"]},
    # Communities
    {"method": "GET", "path": "/communities", "name": "get_communities", "section": "Communities",
     "doc": "List all communities", "paginated": True},
    {"method": "GET", "path": "/communities/{community_id}", "name": "get_community", "section": "Communities",
     "doc": "Get a specific community by ID", "path_params": ["community_id"]},
    # Events
    {"method": "GET", "path": "/events", "name": "get_events", "section": "Events",
     "doc": "List all events", "paginated": True},
    {"method": "GET", "path": "/events/{event_id}", "name": "get_event", "section": "Events",
     "doc": "Get a specific event by ID", "path_params": ["event_id"]},
    # Search
    {"method": "GET", "path": "/search", "name": "search", "section": "Search",
     "doc": "Search across Interact intranet content",
     "query_params": [("q", "q", "str", "Search query string")],
     "extra_query": [("type", "type", "str | None", "Filter by content type", True),
                     ("limit", "limit", "int | None", "Maximum number of results to return", True),
                     ("offset", "offset", "int | None", "Number of results to skip", True)]},
]


def _gen_method(ep: dict) -> str:
    """Generate a single async method from an endpoint spec."""
    name = ep["name"]
    method = ep["method"]
    path = ep["path"]
    doc = ep["doc"]
    path_params = ep.get("path_params", [])
    paginated = ep.get("paginated", False)
    body_params = ep.get("body_params", [])
    query_params = ep.get("query_params", [])
    extra_query = ep.get("extra_query", [])
    has_query = paginated or query_params or extra_query

    sig_parts = ["self"]
    for p in path_params:
        sig_parts.append(f"{p}: str")
    for qp in query_params:
        sig_parts.append(f"{qp[0]}: {qp[2]}")
    for bp in body_params:
        sig_parts.append(f"{bp[0]}: {bp[2]}")
    if paginated or extra_query:
        sig_parts.append("*")
        if paginated:
            sig_parts.append("limit: int | None = None")
            sig_parts.append("offset: int | None = None")
        for eq in extra_query:
            sig_parts.append(f"{eq[0]}: {eq[2]} = None")

    sig = ",\n        ".join(sig_parts)

    doc_args = ""
    if path_params or paginated or body_params or query_params or extra_query:
        doc_args = "\n        Args:\n"
        for p in path_params:
            doc_args += f"            {p}: The {p.replace('_', ' ')}\n"
        for qp in query_params:
            doc_args += f"            {qp[0]}: {qp[3]}\n"
        for bp in body_params:
            doc_args += f"            {bp[0]}: {bp[3]}\n"
        if paginated:
            doc_args += "            limit: Maximum number of results to return\n"
            doc_args += "            offset: Number of results to skip\n"
        for eq in extra_query:
            doc_args += f"            {eq[0]}: {eq[3]}\n"

    query_block = ""
    if has_query:
        lines = ["", "        query_params: dict[str, Any] = {}"]
        for qp in query_params:
            lines.append(f"        query_params['{qp[1]}'] = {qp[0]}")
        if paginated:
            lines.append("        if limit is not None:")
            lines.append("            query_params['limit'] = str(limit)")
            lines.append("        if offset is not None:")
            lines.append("            query_params['offset'] = str(offset)")
        for eq in extra_query:
            lines.append(f"        if {eq[0]} is not None:")
            if "int" in eq[2]:
                lines.append(f"            query_params['{eq[1]}'] = str({eq[0]})")
            else:
                lines.append(f"            query_params['{eq[1]}'] = {eq[0]}")
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
    if has_query:
        req_extra += "\n                query=query_params,"
    if body_params:
        req_extra += "\n                body=body,"

    return f'''
    async def {name}(
        {sig},
    ) -> InteractResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            InteractResponse with operation result
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
            return InteractResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return InteractResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full Interact DataSource module code."""
    header = '''# ruff: noqa
"""
Interact (Interact Intranet) REST API DataSource - Auto-generated API wrapper

Generated from Interact REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.interact.interact import InteractClient, InteractResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class InteractDataSource:
    """Interact REST API DataSource

    Provides async wrapper methods for Interact REST API operations:
    - Users management
    - Content management
    - Pages management
    - News management
    - Communities management
    - Events management
    - Search

    The base URL is https://api.interact-intranet.com/v1.

    All methods return InteractResponse objects.
    """

    def __init__(self, client: InteractClient) -> None:
        """Initialize with InteractClient.

        Args:
            client: InteractClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'InteractDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> InteractClient:
        """Return the underlying InteractClient."""
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
