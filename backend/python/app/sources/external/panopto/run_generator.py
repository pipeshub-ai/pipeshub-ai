# ruff: noqa
"""
Panopto DataSource Code Generator

This script generates the PanoptoDataSource class with all API endpoint
wrapper methods based on the Panopto REST API v1 specification.

The generated code follows the pattern established by ClickUp and other
connectors in this project, using HTTPRequest/HTTPResponse for all API calls.

Usage:
    python -m app.sources.external.panopto.run_generator

Output:
    Prints the generated Python source code for the PanoptoDataSource class
    to stdout. Redirect to a file to save:

    python -m app.sources.external.panopto.run_generator > \
        app/sources/external/panopto/panopto.py
"""

from __future__ import annotations

ENDPOINTS = [
    {
        "name": "get_sessions",
        "method": "GET",
        "path": "/sessions",
        "doc": "Get all sessions (recordings)",
        "path_params": [],
        "query_params": [
            ("folder_id", "str | None", "folderId", "Filter by folder ID"),
            ("search_query", "str | None", "searchQuery", "Search query string"),
            ("sort_field", "str | None", "sortField", "Field to sort by"),
            ("sort_order", "str | None", "sortOrder", "Sort order (Asc or Desc)"),
            ("page_number", "int | None", "pageNumber", "Page number for pagination"),
            ("max_number_results", "int | None", "maxNumberResults", "Maximum number of results per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_session",
        "method": "GET",
        "path": "/sessions/{session_id}",
        "doc": "Get a specific session by ID",
        "path_params": [("session_id", "str", "The session (recording) ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_session_viewers",
        "method": "GET",
        "path": "/sessions/{session_id}/viewers",
        "doc": "Get viewers of a specific session",
        "path_params": [("session_id", "str", "The session (recording) ID")],
        "query_params": [
            ("page_number", "int | None", "pageNumber", "Page number for pagination"),
            ("max_number_results", "int | None", "maxNumberResults", "Maximum number of results per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_folders",
        "method": "GET",
        "path": "/folders",
        "doc": "Get all folders",
        "path_params": [],
        "query_params": [
            ("parent_folder_id", "str | None", "parentFolderId", "Filter by parent folder ID"),
            ("search_query", "str | None", "searchQuery", "Search query string"),
            ("sort_field", "str | None", "sortField", "Field to sort by"),
            ("sort_order", "str | None", "sortOrder", "Sort order (Asc or Desc)"),
            ("page_number", "int | None", "pageNumber", "Page number for pagination"),
            ("max_number_results", "int | None", "maxNumberResults", "Maximum number of results per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_folder",
        "method": "GET",
        "path": "/folders/{folder_id}",
        "doc": "Get a specific folder by ID",
        "path_params": [("folder_id", "str", "The folder ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_folder_sessions",
        "method": "GET",
        "path": "/folders/{folder_id}/sessions",
        "doc": "Get sessions in a specific folder",
        "path_params": [("folder_id", "str", "The folder ID")],
        "query_params": [
            ("sort_field", "str | None", "sortField", "Field to sort by"),
            ("sort_order", "str | None", "sortOrder", "Sort order (Asc or Desc)"),
            ("page_number", "int | None", "pageNumber", "Page number for pagination"),
            ("max_number_results", "int | None", "maxNumberResults", "Maximum number of results per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_users",
        "method": "GET",
        "path": "/users",
        "doc": "Get all users",
        "path_params": [],
        "query_params": [
            ("search_query", "str | None", "searchQuery", "Search query string"),
            ("sort_field", "str | None", "sortField", "Field to sort by"),
            ("sort_order", "str | None", "sortOrder", "Sort order (Asc or Desc)"),
            ("page_number", "int | None", "pageNumber", "Page number for pagination"),
            ("max_number_results", "int | None", "maxNumberResults", "Maximum number of results per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_user",
        "method": "GET",
        "path": "/users/{user_id}",
        "doc": "Get a specific user by ID",
        "path_params": [("user_id", "str", "The user ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_groups",
        "method": "GET",
        "path": "/groups",
        "doc": "Get all groups",
        "path_params": [],
        "query_params": [
            ("page_number", "int | None", "pageNumber", "Page number for pagination"),
            ("max_number_results", "int | None", "maxNumberResults", "Maximum number of results per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_group",
        "method": "GET",
        "path": "/groups/{group_id}",
        "doc": "Get a specific group by ID",
        "path_params": [("group_id", "str", "The group ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "search",
        "method": "GET",
        "path": "/search",
        "doc": "Search for sessions and folders",
        "path_params": [],
        "query_params": [
            ("query", "str", "query", "Search query string (required)"),
            ("page_number", "int | None", "pageNumber", "Page number for pagination"),
            ("max_number_results", "int | None", "maxNumberResults", "Maximum number of results per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_view_stats",
        "method": "GET",
        "path": "/stats/views",
        "doc": "Get view statistics for a session",
        "path_params": [],
        "query_params": [
            ("session_id", "str", "sessionId", "The session ID to get stats for (required)"),
            ("page_number", "int | None", "pageNumber", "Page number for pagination"),
            ("max_number_results", "int | None", "maxNumberResults", "Maximum number of results per page"),
        ],
        "body_params": [],
    },
]


def generate_method(ep: dict) -> str:
    """Generate a single async method for an endpoint."""
    name = ep["name"]
    method = ep["method"]
    path = ep["path"]
    doc = ep["doc"]
    path_params = ep.get("path_params", [])
    query_params = ep.get("query_params", [])

    sig_parts = ["self"]
    for pp_name, pp_type, _pp_doc in path_params:
        sig_parts.append(f"{pp_name}: {pp_type}")

    required_qp = [q for q in query_params if "None" not in q[1]]
    optional_qp = [q for q in query_params if "None" in q[1]]

    if required_qp or optional_qp:
        sig_parts.append("*")
    for qp_name, qp_type, _qp_api, _qp_doc in required_qp:
        sig_parts.append(f"{qp_name}: {qp_type}")
    for qp_name, qp_type, _qp_api, _qp_doc in optional_qp:
        sig_parts.append(f"{qp_name}: {qp_type} = None")

    sig = ",\n        ".join(sig_parts)

    doc_args = []
    for pp_name, _pp_type, pp_doc in path_params:
        doc_args.append(f"            {pp_name}: {pp_doc}")
    for qp_name, _qp_type, _qp_api, qp_doc in query_params:
        doc_args.append(f"            {qp_name}: {qp_doc}")

    args_section = ""
    if doc_args:
        args_section = "\n\n        Args:\n" + "\n".join(doc_args)

    qp_block = ""
    if query_params:
        qp_block = "\n        query_params: dict[str, Any] = {}\n"
        for qp_name, qp_type, qp_api, _ in query_params:
            if "None" not in qp_type:
                if "int" in qp_type:
                    qp_block += f"        query_params['{qp_api}'] = str({qp_name})\n"
                else:
                    qp_block += f"        query_params['{qp_api}'] = {qp_name}\n"
            else:
                if "int" in qp_type:
                    qp_block += f"        if {qp_name} is not None:\n            query_params['{qp_api}'] = str({qp_name})\n"
                else:
                    qp_block += f"        if {qp_name} is not None:\n            query_params['{qp_api}'] = {qp_name}\n"

    if path_params:
        format_args = ", ".join(f"{p[0]}={p[0]}" for p in path_params)
        url_line = f'        url = self.base_url + "{path}".format({format_args})'
    else:
        url_line = f'        url = self.base_url + "{path}"'

    req_kwargs = f'method="{method}",\n                url=url,\n                headers={{"Content-Type": "application/json"}}'
    if query_params:
        req_kwargs += ",\n                query=query_params"

    return f'''    async def {name}(
        {sig}
    ) -> PanoptoResponse:
        """{doc}{args_section}

        Returns:
            PanoptoResponse with operation result
        """
{qp_block}{url_line}

        try:
            request = HTTPRequest(
                {req_kwargs},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PanoptoResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return PanoptoResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full PanoptoDataSource module."""
    header = '''# ruff: noqa
"""
Panopto REST API DataSource - Auto-generated API wrapper

Generated from Panopto REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.panopto.panopto import PanoptoClient, PanoptoResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class PanoptoDataSource:
    """Panopto REST API DataSource

    Provides async wrapper methods for Panopto REST API operations.
    All methods return PanoptoResponse objects.
    """

    def __init__(self, client: PanoptoClient) -> None:
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'PanoptoDataSource':
        return self

    def get_client(self) -> PanoptoClient:
        return self._client

'''
    methods = "\n".join(generate_method(ep) for ep in ENDPOINTS)
    return header + methods


if __name__ == "__main__":
    print(generate_datasource())
