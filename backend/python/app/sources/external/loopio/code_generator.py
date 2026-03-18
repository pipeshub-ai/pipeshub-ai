# ruff: noqa
"""
Loopio DataSource Code Generator

Defines Loopio API endpoint specifications and generates the DataSource
wrapper class (loopio.py) from them.

Endpoints:
  /projects, /projects/{id}, /entries, /entries/{id}, /library, /library/{id},
  /categories, /categories/{id}, /users, /users/{id}, /groups, /groups/{id},
  /tags, /tags/{id}
"""

from __future__ import annotations

ENDPOINTS = [
    # Projects
    {"method": "GET", "path": "/projects", "name": "get_projects", "section": "Projects",
     "doc": "List all projects", "paginated": True},
    {"method": "GET", "path": "/projects/{project_id}", "name": "get_project", "section": "Projects",
     "doc": "Get a specific project by ID", "path_params": ["project_id"]},
    # Entries
    {"method": "GET", "path": "/entries", "name": "get_entries", "section": "Entries",
     "doc": "List all entries", "paginated": True},
    {"method": "GET", "path": "/entries/{entry_id}", "name": "get_entry", "section": "Entries",
     "doc": "Get a specific entry by ID", "path_params": ["entry_id"]},
    # Library
    {"method": "GET", "path": "/library", "name": "get_library_items", "section": "Library",
     "doc": "List all library items", "paginated": True},
    {"method": "GET", "path": "/library/{library_id}", "name": "get_library_item", "section": "Library",
     "doc": "Get a specific library item by ID", "path_params": ["library_id"]},
    # Categories
    {"method": "GET", "path": "/categories", "name": "get_categories", "section": "Categories",
     "doc": "List all categories", "paginated": True},
    {"method": "GET", "path": "/categories/{category_id}", "name": "get_category", "section": "Categories",
     "doc": "Get a specific category by ID", "path_params": ["category_id"]},
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
    # Tags
    {"method": "GET", "path": "/tags", "name": "get_tags", "section": "Tags",
     "doc": "List all tags", "paginated": True},
    {"method": "GET", "path": "/tags/{tag_id}", "name": "get_tag", "section": "Tags",
     "doc": "Get a specific tag by ID", "path_params": ["tag_id"]},
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

    sig_parts = ["self"]
    for p in path_params:
        sig_parts.append(f"{p}: str")
    for bp in body_params:
        sig_parts.append(f"{bp[0]}: {bp[2]}")
    if paginated:
        sig_parts.append("*")
        sig_parts.append("limit: int | None = None")
        sig_parts.append("offset: int | None = None")

    sig = ",\n        ".join(sig_parts)

    doc_args = ""
    if path_params or paginated or body_params:
        doc_args = "\n        Args:\n"
        for p in path_params:
            doc_args += f"            {p}: The {p.replace('_', ' ')}\n"
        for bp in body_params:
            doc_args += f"            {bp[0]}: {bp[3]}\n"
        if paginated:
            doc_args += "            limit: Maximum number of results to return\n"
            doc_args += "            offset: Number of results to skip\n"

    query_block = ""
    if paginated:
        query_block = """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)
"""

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
    if paginated:
        req_extra += "\n                query=query_params,"
    if body_params:
        req_extra += "\n                body=body,"

    return f'''
    async def {name}(
        {sig},
    ) -> LoopioResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            LoopioResponse with operation result
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
            return LoopioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return LoopioResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full Loopio DataSource module code."""
    header = '''# ruff: noqa
"""
Loopio REST API DataSource - Auto-generated API wrapper

Generated from Loopio REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.loopio.loopio import LoopioClient, LoopioResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class LoopioDataSource:
    """Loopio REST API DataSource

    Provides async wrapper methods for Loopio REST API operations:
    - Projects management
    - Entries management
    - Library management
    - Categories management
    - Users management
    - Groups management
    - Tags management

    The base URL is https://api.loopio.com/v1.

    All methods return LoopioResponse objects.
    """

    def __init__(self, client: LoopioClient) -> None:
        """Initialize with LoopioClient.

        Args:
            client: LoopioClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'LoopioDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> LoopioClient:
        """Return the underlying LoopioClient."""
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
