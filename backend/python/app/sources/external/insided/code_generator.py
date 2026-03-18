# ruff: noqa
"""
InSided DataSource Code Generator

Defines InSided API endpoint specifications and generates the DataSource
wrapper class (insided.py) from them.

Endpoints:
  /communities, /communities/{id}, /categories, /categories/{id},
  /topics, /topics/{id}, /posts, /posts/{id}, /users, /users/{id},
  /groups, /groups/{id}, /search
"""

from __future__ import annotations

ENDPOINTS = [
    # Communities
    {"method": "GET", "path": "/communities", "name": "get_communities", "section": "Communities",
     "doc": "List all communities", "paginated": True},
    {"method": "GET", "path": "/communities/{community_id}", "name": "get_community", "section": "Communities",
     "doc": "Get a specific community by ID", "path_params": ["community_id"]},
    # Categories
    {"method": "GET", "path": "/categories", "name": "get_categories", "section": "Categories",
     "doc": "List all categories", "paginated": True},
    {"method": "GET", "path": "/categories/{category_id}", "name": "get_category", "section": "Categories",
     "doc": "Get a specific category by ID", "path_params": ["category_id"]},
    # Topics
    {"method": "GET", "path": "/topics", "name": "get_topics", "section": "Topics",
     "doc": "List all topics", "paginated": True},
    {"method": "GET", "path": "/topics/{topic_id}", "name": "get_topic", "section": "Topics",
     "doc": "Get a specific topic by ID", "path_params": ["topic_id"]},
    # Posts
    {"method": "GET", "path": "/posts", "name": "get_posts", "section": "Posts",
     "doc": "List all posts", "paginated": True},
    {"method": "GET", "path": "/posts/{post_id}", "name": "get_post", "section": "Posts",
     "doc": "Get a specific post by ID", "path_params": ["post_id"]},
    # Users
    {"method": "GET", "path": "/users", "name": "get_users", "section": "Users",
     "doc": "List all users", "paginated": True},
    {"method": "GET", "path": "/users/{user_id}", "name": "get_user", "section": "Users",
     "doc": "Get a specific user by ID", "path_params": ["user_id"]},
    # Groups
    {"method": "GET", "path": "/groups", "name": "get_groups", "section": "Groups",
     "doc": "List all groups", "paginated": True},
    {"method": "GET", "path": "/groups/{group_id}", "name": "get_group", "section": "Groups",
     "doc": "Get a specific group by ID", "path_params": ["group_id"]},
    # Search
    {"method": "GET", "path": "/search", "name": "search", "section": "Search",
     "doc": "Search across communities content",
     "query_params": [("q", "q", "str", "Search query string")], "paginated": True},
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
    query_extra = ep.get("query_params", [])

    sig_parts = ["self"]
    for p in path_params:
        sig_parts.append(f"{p}: str")
    for qp in query_extra:
        sig_parts.append(f"{qp[0]}: {qp[2]}")
    for bp in body_params:
        sig_parts.append(f"{bp[0]}: {bp[2]}")
    if paginated or query_extra:
        if "*" not in sig_parts:
            sig_parts.append("*")
    if paginated:
        sig_parts.append("limit: int | None = None")
        sig_parts.append("offset: int | None = None")

    sig = ",\n        ".join(sig_parts)

    doc_args = ""
    if path_params or paginated or body_params or query_extra:
        doc_args = "\n        Args:\n"
        for p in path_params:
            doc_args += f"            {p}: The {p.replace('_', ' ')}\n"
        for qp in query_extra:
            doc_args += f"            {qp[0]}: {qp[3]}\n"
        for bp in body_params:
            doc_args += f"            {bp[0]}: {bp[3]}\n"
        if paginated:
            doc_args += "            limit: Maximum number of results to return\n"
            doc_args += "            offset: Number of results to skip\n"

    query_block = ""
    if paginated or query_extra:
        lines = ["", "        query_params: dict[str, Any] = {}"]
        for qp in query_extra:
            lines.append(f"        query_params['{qp[1]}'] = {qp[0]}")
        if paginated:
            lines.append("        if limit is not None:")
            lines.append("            query_params['limit'] = str(limit)")
            lines.append("        if offset is not None:")
            lines.append("            query_params['offset'] = str(offset)")
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
    if paginated or query_extra:
        req_extra += "\n                query=query_params,"
    if body_params:
        req_extra += "\n                body=body,"

    return f'''
    async def {name}(
        {sig},
    ) -> InSidedResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            InSidedResponse with operation result
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
            return InSidedResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return InSidedResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full InSided DataSource module code."""
    header = '''# ruff: noqa
"""
InSided (Gainsight Customer Communities) REST API DataSource - Auto-generated API wrapper

Generated from InSided REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.insided.insided import InSidedClient, InSidedResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class InSidedDataSource:
    """InSided REST API DataSource

    Provides async wrapper methods for InSided REST API operations:
    - Communities management
    - Categories management
    - Topics management
    - Posts management
    - Users management
    - Groups management
    - Search

    The base URL is https://api.insided.com/v2.

    All methods return InSidedResponse objects.
    """

    def __init__(self, client: InSidedClient) -> None:
        """Initialize with InSidedClient.

        Args:
            client: InSidedClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'InSidedDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> InSidedClient:
        """Return the underlying InSidedClient."""
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
