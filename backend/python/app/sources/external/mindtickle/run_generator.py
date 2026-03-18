# ruff: noqa
"""
Mindtickle DataSource Code Generator

This script generates the MindtickleDataSource class with all API endpoint
wrapper methods based on the Mindtickle REST API v2 specification.

The generated code follows the pattern established by ClickUp and other
connectors in this project, using HTTPRequest/HTTPResponse for all API calls.

Usage:
    python -m app.sources.external.mindtickle.run_generator

Output:
    Prints the generated Python source code for the MindtickleDataSource class
    to stdout. Redirect to a file to save:

    python -m app.sources.external.mindtickle.run_generator > \
        app/sources/external/mindtickle/mindtickle.py
"""

from __future__ import annotations

ENDPOINTS = [
    {
        "name": "get_users",
        "method": "GET",
        "path": "/users",
        "doc": "Get all users",
        "path_params": [],
        "query_params": [
            ("page", "int | None", "page", "Page number for pagination"),
            ("page_size", "int | None", "page_size", "Number of records per page"),
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
        "name": "get_courses",
        "method": "GET",
        "path": "/courses",
        "doc": "Get all courses",
        "path_params": [],
        "query_params": [
            ("page", "int | None", "page", "Page number for pagination"),
            ("page_size", "int | None", "page_size", "Number of records per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_course",
        "method": "GET",
        "path": "/courses/{course_id}",
        "doc": "Get a specific course by ID",
        "path_params": [("course_id", "str", "The course ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_modules",
        "method": "GET",
        "path": "/modules",
        "doc": "Get all modules",
        "path_params": [],
        "query_params": [
            ("page", "int | None", "page", "Page number for pagination"),
            ("page_size", "int | None", "page_size", "Number of records per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_module",
        "method": "GET",
        "path": "/modules/{module_id}",
        "doc": "Get a specific module by ID",
        "path_params": [("module_id", "str", "The module ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_quizzes",
        "method": "GET",
        "path": "/quizzes",
        "doc": "Get all quizzes",
        "path_params": [],
        "query_params": [
            ("page", "int | None", "page", "Page number for pagination"),
            ("page_size", "int | None", "page_size", "Number of records per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_quiz",
        "method": "GET",
        "path": "/quizzes/{quiz_id}",
        "doc": "Get a specific quiz by ID",
        "path_params": [("quiz_id", "str", "The quiz ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_assessments",
        "method": "GET",
        "path": "/assessments",
        "doc": "Get all assessments",
        "path_params": [],
        "query_params": [
            ("page", "int | None", "page", "Page number for pagination"),
            ("page_size", "int | None", "page_size", "Number of records per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_assessment",
        "method": "GET",
        "path": "/assessments/{assessment_id}",
        "doc": "Get a specific assessment by ID",
        "path_params": [("assessment_id", "str", "The assessment ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_content",
        "method": "GET",
        "path": "/content",
        "doc": "Get all content items",
        "path_params": [],
        "query_params": [
            ("page", "int | None", "page", "Page number for pagination"),
            ("page_size", "int | None", "page_size", "Number of records per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_content_item",
        "method": "GET",
        "path": "/content/{content_id}",
        "doc": "Get a specific content item by ID",
        "path_params": [("content_id", "str", "The content item ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_leaderboard",
        "method": "GET",
        "path": "/leaderboard",
        "doc": "Get the leaderboard",
        "path_params": [],
        "query_params": [
            ("page", "int | None", "page", "Page number for pagination"),
            ("page_size", "int | None", "page_size", "Number of records per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_completion_analytics",
        "method": "GET",
        "path": "/analytics/completion",
        "doc": "Get completion analytics",
        "path_params": [],
        "query_params": [
            ("page", "int | None", "page", "Page number for pagination"),
            ("page_size", "int | None", "page_size", "Number of records per page"),
            ("start_date", "str | None", "start_date", "Start date filter (ISO 8601)"),
            ("end_date", "str | None", "end_date", "End date filter (ISO 8601)"),
        ],
        "body_params": [],
    },
    {
        "name": "get_engagement_analytics",
        "method": "GET",
        "path": "/analytics/engagement",
        "doc": "Get engagement analytics",
        "path_params": [],
        "query_params": [
            ("page", "int | None", "page", "Page number for pagination"),
            ("page_size", "int | None", "page_size", "Number of records per page"),
            ("start_date", "str | None", "start_date", "Start date filter (ISO 8601)"),
            ("end_date", "str | None", "end_date", "End date filter (ISO 8601)"),
        ],
        "body_params": [],
    },
    {
        "name": "get_series",
        "method": "GET",
        "path": "/series",
        "doc": "Get all series",
        "path_params": [],
        "query_params": [
            ("page", "int | None", "page", "Page number for pagination"),
            ("page_size", "int | None", "page_size", "Number of records per page"),
        ],
        "body_params": [],
    },
    {
        "name": "get_series_item",
        "method": "GET",
        "path": "/series/{series_id}",
        "doc": "Get a specific series by ID",
        "path_params": [("series_id", "str", "The series ID")],
        "query_params": [],
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
    ) -> MindtickleResponse:
        """{doc}{args_section}

        Returns:
            MindtickleResponse with operation result
        """
{qp_block}{url_line}

        try:
            request = HTTPRequest(
                {req_kwargs},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MindtickleResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return MindtickleResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full MindtickleDataSource module."""
    header = '''# ruff: noqa
"""
Mindtickle REST API DataSource - Auto-generated API wrapper

Generated from Mindtickle REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.mindtickle.mindtickle import MindtickleClient, MindtickleResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class MindtickleDataSource:
    """Mindtickle REST API DataSource

    Provides async wrapper methods for Mindtickle REST API operations.
    All methods return MindtickleResponse objects.
    """

    def __init__(self, client: MindtickleClient) -> None:
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'MindtickleDataSource':
        return self

    def get_client(self) -> MindtickleClient:
        return self._client

'''
    methods = "\n".join(generate_method(ep) for ep in ENDPOINTS)
    return header + methods


if __name__ == "__main__":
    print(generate_datasource())
