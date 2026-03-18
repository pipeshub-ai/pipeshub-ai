# ruff: noqa
"""
Facebook Workplace (Meta Workplace) DataSource Code Generator

This script generates the WorkplaceDataSource class with all API endpoint
wrapper methods based on the Facebook Graph API v18.0 specification for Workplace.

The generated code follows the pattern established by ClickUp and other
connectors in this project, using HTTPRequest/HTTPResponse for all API calls.

Usage:
    python -m app.sources.external.workplace.run_generator

Output:
    Prints the generated Python source code for the WorkplaceDataSource class
    to stdout. Redirect to a file to save:

    python -m app.sources.external.workplace.run_generator > \
        app/sources/external/workplace/workplace.py
"""

from __future__ import annotations

ENDPOINTS = [
    {
        "name": "get_me",
        "method": "GET",
        "path": "/me",
        "doc": "Get the current authenticated user",
        "path_params": [],
        "query_params": [
            ("fields", "str | None", "fields", "Comma-separated list of fields to include"),
        ],
        "body_params": [],
    },
    {
        "name": "get_community_members",
        "method": "GET",
        "path": "/community/members",
        "doc": "Get community members",
        "path_params": [],
        "query_params": [
            ("limit", "int | None", "limit", "Maximum number of results per page"),
            ("after", "str | None", "after", "Cursor for pagination (next page)"),
            ("fields", "str | None", "fields", "Comma-separated list of fields to include"),
        ],
        "body_params": [],
    },
    {
        "name": "get_user",
        "method": "GET",
        "path": "/{user_id}",
        "doc": "Get a specific user by ID",
        "path_params": [("user_id", "str", "The user ID")],
        "query_params": [
            ("fields", "str | None", "fields", "Comma-separated list of fields to include"),
        ],
        "body_params": [],
    },
    {
        "name": "get_user_feed",
        "method": "GET",
        "path": "/{user_id}/feed",
        "doc": "Get a user's feed",
        "path_params": [("user_id", "str", "The user ID")],
        "query_params": [
            ("limit", "int | None", "limit", "Maximum number of results per page"),
            ("after", "str | None", "after", "Cursor for pagination (next page)"),
            ("fields", "str | None", "fields", "Comma-separated list of fields to include"),
        ],
        "body_params": [],
    },
    {
        "name": "get_community_groups",
        "method": "GET",
        "path": "/community/groups",
        "doc": "Get community groups",
        "path_params": [],
        "query_params": [
            ("limit", "int | None", "limit", "Maximum number of results per page"),
            ("after", "str | None", "after", "Cursor for pagination (next page)"),
            ("fields", "str | None", "fields", "Comma-separated list of fields to include"),
        ],
        "body_params": [],
    },
    {
        "name": "get_group",
        "method": "GET",
        "path": "/{group_id}",
        "doc": "Get a specific group by ID",
        "path_params": [("group_id", "str", "The group ID")],
        "query_params": [
            ("fields", "str | None", "fields", "Comma-separated list of fields to include"),
        ],
        "body_params": [],
    },
    {
        "name": "get_group_feed",
        "method": "GET",
        "path": "/{group_id}/feed",
        "doc": "Get a group's feed",
        "path_params": [("group_id", "str", "The group ID")],
        "query_params": [
            ("limit", "int | None", "limit", "Maximum number of results per page"),
            ("after", "str | None", "after", "Cursor for pagination (next page)"),
            ("fields", "str | None", "fields", "Comma-separated list of fields to include"),
        ],
        "body_params": [],
    },
    {
        "name": "get_group_members",
        "method": "GET",
        "path": "/{group_id}/members",
        "doc": "Get members of a group",
        "path_params": [("group_id", "str", "The group ID")],
        "query_params": [
            ("limit", "int | None", "limit", "Maximum number of results per page"),
            ("after", "str | None", "after", "Cursor for pagination (next page)"),
            ("fields", "str | None", "fields", "Comma-separated list of fields to include"),
        ],
        "body_params": [],
    },
    {
        "name": "get_post",
        "method": "GET",
        "path": "/{post_id}",
        "doc": "Get a specific post by ID",
        "path_params": [("post_id", "str", "The post ID")],
        "query_params": [
            ("fields", "str | None", "fields", "Comma-separated list of fields to include"),
        ],
        "body_params": [],
    },
    {
        "name": "get_post_comments",
        "method": "GET",
        "path": "/{post_id}/comments",
        "doc": "Get comments on a specific post",
        "path_params": [("post_id", "str", "The post ID")],
        "query_params": [
            ("limit", "int | None", "limit", "Maximum number of results per page"),
            ("after", "str | None", "after", "Cursor for pagination (next page)"),
            ("fields", "str | None", "fields", "Comma-separated list of fields to include"),
        ],
        "body_params": [],
    },
    {
        "name": "get_community_feeds",
        "method": "GET",
        "path": "/community/feeds",
        "doc": "Get community feeds",
        "path_params": [],
        "query_params": [
            ("limit", "int | None", "limit", "Maximum number of results per page"),
            ("after", "str | None", "after", "Cursor for pagination (next page)"),
            ("fields", "str | None", "fields", "Comma-separated list of fields to include"),
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

    optional_qp = [q for q in query_params if "None" in q[1]]
    if optional_qp:
        sig_parts.append("*")
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
    ) -> WorkplaceResponse:
        """{doc}{args_section}

        Returns:
            WorkplaceResponse with operation result
        """
{qp_block}{url_line}

        try:
            request = HTTPRequest(
                {req_kwargs},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WorkplaceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return WorkplaceResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full WorkplaceDataSource module."""
    header = '''# ruff: noqa
"""
Facebook Workplace (Meta Workplace) REST API DataSource - Auto-generated API wrapper

Generated from Facebook Graph API v18.0 documentation for Workplace.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.workplace.workplace import WorkplaceClient, WorkplaceResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class WorkplaceDataSource:
    """Facebook Workplace REST API DataSource

    Provides async wrapper methods for Facebook Workplace API operations.
    All methods return WorkplaceResponse objects.
    """

    def __init__(self, client: WorkplaceClient) -> None:
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'WorkplaceDataSource':
        return self

    def get_client(self) -> WorkplaceClient:
        return self._client

'''
    methods = "\n".join(generate_method(ep) for ep in ENDPOINTS)
    return header + methods


if __name__ == "__main__":
    print(generate_datasource())
