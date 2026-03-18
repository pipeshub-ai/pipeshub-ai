# ruff: noqa
"""
PlusPlus DataSource Code Generator

Defines PlusPlus API endpoint specifications and generates the DataSource
wrapper class (plusplus.py) from them.

Endpoints:
  /events, /events/{id}, /users, /users/{id}, /tracks, /tracks/{id},
  /channels, /channels/{id}, /content, /content/{id}, /tags, /enrollments
"""

from __future__ import annotations

ENDPOINTS = [
    # Events
    {"method": "GET", "path": "/events", "name": "list_events", "section": "Events",
     "doc": "List all events", "paginated": True},
    {"method": "GET", "path": "/events/{event_id}", "name": "get_event", "section": "Events",
     "doc": "Get a specific event by ID", "path_params": ["event_id"]},
    # Users
    {"method": "GET", "path": "/users", "name": "list_users", "section": "Users",
     "doc": "List all users", "paginated": True},
    {"method": "GET", "path": "/users/{user_id}", "name": "get_user", "section": "Users",
     "doc": "Get a specific user by ID", "path_params": ["user_id"]},
    # Tracks
    {"method": "GET", "path": "/tracks", "name": "list_tracks", "section": "Tracks",
     "doc": "List all tracks", "paginated": True},
    {"method": "GET", "path": "/tracks/{track_id}", "name": "get_track", "section": "Tracks",
     "doc": "Get a specific track by ID", "path_params": ["track_id"]},
    # Channels
    {"method": "GET", "path": "/channels", "name": "list_channels", "section": "Channels",
     "doc": "List all channels", "paginated": True},
    {"method": "GET", "path": "/channels/{channel_id}", "name": "get_channel", "section": "Channels",
     "doc": "Get a specific channel by ID", "path_params": ["channel_id"]},
    # Content
    {"method": "GET", "path": "/content", "name": "list_content", "section": "Content",
     "doc": "List all content", "paginated": True},
    {"method": "GET", "path": "/content/{content_id}", "name": "get_content", "section": "Content",
     "doc": "Get a specific content item by ID", "path_params": ["content_id"]},
    # Tags
    {"method": "GET", "path": "/tags", "name": "list_tags", "section": "Tags",
     "doc": "List all tags", "paginated": True},
    # Enrollments
    {"method": "GET", "path": "/enrollments", "name": "list_enrollments", "section": "Enrollments",
     "doc": "List all enrollments", "paginated": True},
]


def _gen_method(ep: dict) -> str:
    """Generate a single async method from an endpoint spec."""
    name = ep["name"]
    method = ep["method"]
    path = ep["path"]
    doc = ep["doc"]
    path_params = ep.get("path_params", [])
    paginated = ep.get("paginated", False)

    # Build signature
    sig_parts = ["self"]
    for p in path_params:
        sig_parts.append(f"{p}: str")
    if paginated:
        sig_parts.append("*")
        sig_parts.append("page: int | None = None")
        sig_parts.append("per_page: int | None = None")

    sig = ",\n        ".join(sig_parts)

    # Build doc args
    doc_args = ""
    if path_params or paginated:
        doc_args = "\n        Args:\n"
        for p in path_params:
            doc_args += f"            {p}: The {p.replace('_', ' ')}\n"
        if paginated:
            doc_args += "            page: Page number for pagination\n"
            doc_args += "            per_page: Number of items per page\n"

    # Build query params
    query_block = ""
    if paginated:
        query_block = """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
"""

    # Build URL
    if path_params:
        fmt_args = ", ".join(f"{p}={p}" for p in path_params)
        url_line = f'        url = self.base_url + "{path}".format({fmt_args})'
    else:
        url_line = f'        url = self.base_url + "{path}"'

    # Build request kwargs
    req_extra = ""
    if paginated:
        req_extra = "\n                query=query_params,"

    return f'''
    async def {name}(
        {sig}
    ) -> PlusPlusResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            PlusPlusResponse with operation result
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
            return PlusPlusResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return PlusPlusResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full PlusPlus DataSource module code."""
    header = '''# ruff: noqa
"""
PlusPlus REST API DataSource - Auto-generated API wrapper

Generated from PlusPlus REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.plusplus.plusplus import PlusPlusClient, PlusPlusResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class PlusPlusDataSource:
    """PlusPlus REST API DataSource

    Provides async wrapper methods for PlusPlus REST API operations:
    - Events management
    - Users management
    - Tracks management
    - Channels management
    - Content management
    - Tags
    - Enrollments

    All methods return PlusPlusResponse objects.
    """

    def __init__(self, client: PlusPlusClient) -> None:
        """Initialize with PlusPlusClient.

        Args:
            client: PlusPlusClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'PlusPlusDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> PlusPlusClient:
        """Return the underlying PlusPlusClient."""
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
