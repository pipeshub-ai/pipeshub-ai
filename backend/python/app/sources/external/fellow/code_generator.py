# ruff: noqa
"""
Fellow DataSource Code Generator

Defines Fellow API endpoint specifications and generates the DataSource
wrapper class (fellow.py) from them.

Endpoints:
  /meetings, /meetings/{id}, /meetings/{id}/notes, /meetings/{id}/action-items,
  /users, /users/{id}, /streams, /streams/{id}, /feedback, /feedback/{id},
  /objectives, /objectives/{id}, /one-on-ones, /one-on-ones/{id}
"""

from __future__ import annotations

ENDPOINTS = [
    # Meetings
    {"method": "GET", "path": "/meetings", "name": "list_meetings", "section": "Meetings",
     "doc": "List all meetings", "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/meetings/{meeting_id}", "name": "get_meeting", "section": "Meetings",
     "doc": "Get a specific meeting by ID", "path_params": ["meeting_id"]},
    {"method": "GET", "path": "/meetings/{meeting_id}/notes", "name": "get_meeting_notes", "section": "Meetings",
     "doc": "Get notes for a specific meeting", "path_params": ["meeting_id"]},
    {"method": "GET", "path": "/meetings/{meeting_id}/action-items", "name": "get_meeting_action_items", "section": "Meetings",
     "doc": "Get action items for a specific meeting", "path_params": ["meeting_id"]},
    # Users
    {"method": "GET", "path": "/users", "name": "list_users", "section": "Users",
     "doc": "List all users", "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/users/{user_id}", "name": "get_user", "section": "Users",
     "doc": "Get a specific user by ID", "path_params": ["user_id"]},
    # Streams
    {"method": "GET", "path": "/streams", "name": "list_streams", "section": "Streams",
     "doc": "List all streams", "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/streams/{stream_id}", "name": "get_stream", "section": "Streams",
     "doc": "Get a specific stream by ID", "path_params": ["stream_id"]},
    # Feedback
    {"method": "GET", "path": "/feedback", "name": "list_feedback", "section": "Feedback",
     "doc": "List all feedback", "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/feedback/{feedback_id}", "name": "get_feedback", "section": "Feedback",
     "doc": "Get a specific feedback item by ID", "path_params": ["feedback_id"]},
    # Objectives
    {"method": "GET", "path": "/objectives", "name": "list_objectives", "section": "Objectives",
     "doc": "List all objectives", "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/objectives/{objective_id}", "name": "get_objective", "section": "Objectives",
     "doc": "Get a specific objective by ID", "path_params": ["objective_id"]},
    # One-on-Ones
    {"method": "GET", "path": "/one-on-ones", "name": "list_one_on_ones", "section": "One-on-Ones",
     "doc": "List all one-on-ones", "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/one-on-ones/{one_on_one_id}", "name": "get_one_on_one", "section": "One-on-Ones",
     "doc": "Get a specific one-on-one by ID", "path_params": ["one_on_one_id"]},
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
        lines = ["\n        body: dict[str, Any] = {}"]
        for bp in body_params:
            lines.append(f'        if {bp[0]} is not None:')
            lines.append(f'            body["{bp[1]}"] = {bp[0]}')
        body_block = "\n".join(lines)

    req_extra = ""
    if query_params:
        req_extra += "\n                query=query_params,"
    if body_params:
        req_extra += "\n                body=body,"

    return f'''
    async def {name}(
        {sig}
    ) -> FellowResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            FellowResponse with operation result
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
            return FellowResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return FellowResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full Fellow DataSource module code."""
    header = '''# ruff: noqa
"""
Fellow REST API DataSource - Auto-generated API wrapper

Generated from Fellow REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.fellow.fellow import FellowClient, FellowResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class FellowDataSource:
    """Fellow REST API DataSource

    Provides async wrapper methods for Fellow REST API operations:
    - Meetings management
    - Meeting notes and action items
    - Users management
    - Streams management
    - Feedback management
    - Objectives management
    - One-on-Ones management

    All methods return FellowResponse objects.
    """

    def __init__(self, client: FellowClient) -> None:
        """Initialize with FellowClient.

        Args:
            client: FellowClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'FellowDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> FellowClient:
        """Return the underlying FellowClient."""
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
