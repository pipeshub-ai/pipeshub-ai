# ruff: noqa
"""
Seismic DataSource Code Generator

Defines Seismic API endpoint specifications and generates the DataSource
wrapper class (seismic.py) from them.

Endpoints:
  /library/content, /library/content/{id}, /library/folders,
  /library/folders/{id}, /teamsites, /teamsites/{id},
  /teamsites/{id}/content, /users, /users/{id},
  /workspace/documents, /workspace/documents/{id},
  /livesend/links, /analytics/content
"""

from __future__ import annotations

ENDPOINTS = [
    # Library Content
    {"method": "GET", "path": "/library/content", "name": "list_library_content", "section": "Library Content",
     "doc": "List all library content", "paginated": True},
    {"method": "GET", "path": "/library/content/{content_id}", "name": "get_library_content", "section": "Library Content",
     "doc": "Get a specific library content item by ID", "path_params": ["content_id"]},
    # Library Folders
    {"method": "GET", "path": "/library/folders", "name": "list_library_folders", "section": "Library Folders",
     "doc": "List all library folders", "paginated": True},
    {"method": "GET", "path": "/library/folders/{folder_id}", "name": "get_library_folder", "section": "Library Folders",
     "doc": "Get a specific library folder by ID", "path_params": ["folder_id"]},
    # Teamsites
    {"method": "GET", "path": "/teamsites", "name": "list_teamsites", "section": "Teamsites",
     "doc": "List all teamsites", "paginated": True},
    {"method": "GET", "path": "/teamsites/{teamsite_id}", "name": "get_teamsite", "section": "Teamsites",
     "doc": "Get a specific teamsite by ID", "path_params": ["teamsite_id"]},
    {"method": "GET", "path": "/teamsites/{teamsite_id}/content", "name": "get_teamsite_content", "section": "Teamsites",
     "doc": "Get content in a specific teamsite", "path_params": ["teamsite_id"], "paginated": True},
    # Users
    {"method": "GET", "path": "/users", "name": "list_users", "section": "Users",
     "doc": "List all users", "paginated": True},
    {"method": "GET", "path": "/users/{user_id}", "name": "get_user", "section": "Users",
     "doc": "Get a specific user by ID", "path_params": ["user_id"]},
    # Workspace Documents
    {"method": "GET", "path": "/workspace/documents", "name": "list_workspace_documents", "section": "Workspace Documents",
     "doc": "List all workspace documents", "paginated": True},
    {"method": "GET", "path": "/workspace/documents/{document_id}", "name": "get_workspace_document", "section": "Workspace Documents",
     "doc": "Get a specific workspace document by ID", "path_params": ["document_id"]},
    # LiveSend Links
    {"method": "GET", "path": "/livesend/links", "name": "list_livesend_links", "section": "LiveSend Links",
     "doc": "List all LiveSend links", "paginated": True},
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
    ) -> SeismicResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            SeismicResponse with operation result
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
            return SeismicResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return SeismicResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full Seismic DataSource module code."""
    header = '''# ruff: noqa
"""
Seismic REST API DataSource - Auto-generated API wrapper

Generated from Seismic REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.seismic.seismic import SeismicClient, SeismicResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class SeismicDataSource:
    """Seismic REST API DataSource

    Provides async wrapper methods for Seismic REST API operations:
    - Library content management
    - Library folders management
    - Teamsites management
    - Users management
    - Workspace documents management
    - LiveSend links
    - Analytics

    All methods return SeismicResponse objects.
    """

    def __init__(self, client: SeismicClient) -> None:
        """Initialize with SeismicClient.

        Args:
            client: SeismicClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'SeismicDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> SeismicClient:
        """Return the underlying SeismicClient."""
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
