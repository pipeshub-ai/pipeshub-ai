# ruff: noqa
"""
Highspot DataSource Code Generator

Defines Highspot API endpoint specifications and generates the DataSource
wrapper class (highspot.py) from them.

Endpoints:
  /spots, /spots/{id}, /spots/{id}/items, /items, /items/{id},
  /pitches, /pitches/{id}, /groups, /groups/{id}, /users, /users/{id},
  /analytics/content, /analytics/engagement
"""

from __future__ import annotations

ENDPOINTS = [
    # Spots
    {"method": "GET", "path": "/spots", "name": "list_spots", "section": "Spots",
     "doc": "List all spots", "paginated": True},
    {"method": "GET", "path": "/spots/{spot_id}", "name": "get_spot", "section": "Spots",
     "doc": "Get a specific spot by ID", "path_params": ["spot_id"]},
    {"method": "GET", "path": "/spots/{spot_id}/items", "name": "get_spot_items", "section": "Spots",
     "doc": "Get items in a specific spot", "path_params": ["spot_id"], "paginated": True},
    # Items
    {"method": "GET", "path": "/items", "name": "list_items", "section": "Items",
     "doc": "List all items", "paginated": True},
    {"method": "GET", "path": "/items/{item_id}", "name": "get_item", "section": "Items",
     "doc": "Get a specific item by ID", "path_params": ["item_id"]},
    # Pitches
    {"method": "GET", "path": "/pitches", "name": "list_pitches", "section": "Pitches",
     "doc": "List all pitches", "paginated": True},
    {"method": "GET", "path": "/pitches/{pitch_id}", "name": "get_pitch", "section": "Pitches",
     "doc": "Get a specific pitch by ID", "path_params": ["pitch_id"]},
    # Groups
    {"method": "GET", "path": "/groups", "name": "list_groups", "section": "Groups",
     "doc": "List all groups", "paginated": True},
    {"method": "GET", "path": "/groups/{group_id}", "name": "get_group", "section": "Groups",
     "doc": "Get a specific group by ID", "path_params": ["group_id"]},
    # Users
    {"method": "GET", "path": "/users", "name": "list_users", "section": "Users",
     "doc": "List all users", "paginated": True},
    {"method": "GET", "path": "/users/{user_id}", "name": "get_user", "section": "Users",
     "doc": "Get a specific user by ID", "path_params": ["user_id"]},
    # Analytics
    {"method": "GET", "path": "/analytics/content", "name": "get_content_analytics", "section": "Analytics",
     "doc": "Get content analytics"},
    {"method": "GET", "path": "/analytics/engagement", "name": "get_engagement_analytics", "section": "Analytics",
     "doc": "Get engagement analytics"},
]


def _gen_method(ep: dict) -> str:
    """Generate a single async method from an endpoint spec."""
    name = ep["name"]
    method = ep["method"]
    path = ep["path"]
    doc = ep["doc"]
    path_params = ep.get("path_params", [])
    paginated = ep.get("paginated", False)

    sig_parts = ["self"]
    for p in path_params:
        sig_parts.append(f"{p}: str")
    if paginated:
        sig_parts.append("*")
        sig_parts.append("page: int | None = None")
        sig_parts.append("per_page: int | None = None")

    sig = ",\n        ".join(sig_parts)

    doc_args = ""
    if path_params or paginated:
        doc_args = "\n        Args:\n"
        for p in path_params:
            doc_args += f"            {p}: The {p.replace('_', ' ')}\n"
        if paginated:
            doc_args += "            page: Page number for pagination\n"
            doc_args += "            per_page: Number of items per page\n"

    query_block = ""
    if paginated:
        query_block = """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
"""

    if path_params:
        fmt_args = ", ".join(f"{p}={p}" for p in path_params)
        url_line = f'        url = self.base_url + "{path}".format({fmt_args})'
    else:
        url_line = f'        url = self.base_url + "{path}"'

    req_extra = ""
    if paginated:
        req_extra = "\n                query=query_params,"

    return f'''
    async def {name}(
        {sig}
    ) -> HighspotResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            HighspotResponse with operation result
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
            return HighspotResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return HighspotResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full Highspot DataSource module code."""
    header = '''# ruff: noqa
"""
Highspot REST API DataSource - Auto-generated API wrapper

Generated from Highspot REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.highspot.highspot import HighspotClient, HighspotResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class HighspotDataSource:
    """Highspot REST API DataSource

    Provides async wrapper methods for Highspot REST API operations:
    - Spots management
    - Items management
    - Pitches management
    - Groups management
    - Users management
    - Analytics (content and engagement)

    All methods return HighspotResponse objects.
    """

    def __init__(self, client: HighspotClient) -> None:
        """Initialize with HighspotClient.

        Args:
            client: HighspotClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'HighspotDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> HighspotClient:
        """Return the underlying HighspotClient."""
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
