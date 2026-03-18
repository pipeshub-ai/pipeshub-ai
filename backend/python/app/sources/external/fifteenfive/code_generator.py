# ruff: noqa
"""
15Five DataSource Code Generator

Defines 15Five API endpoint specifications and generates the DataSource
wrapper class (fifteenfive.py) from them.

Endpoints:
  /user, /user/{id}, /report, /report/{id}, /review, /review/{id},
  /objective, /objective/{id}, /pulse, /pulse/{id}, /group, /group/{id},
  /department, /department/{id}, /high-five, /high-five/{id},
  /one-on-one, /one-on-one/{id}
"""

from __future__ import annotations

ENDPOINTS = [
    # Users
    {"method": "GET", "path": "/user", "name": "list_users", "section": "Users",
     "doc": "List all users", "paginated": True},
    {"method": "GET", "path": "/user/{user_id}", "name": "get_user", "section": "Users",
     "doc": "Get a specific user by ID", "path_params": ["user_id"]},
    # Reports
    {"method": "GET", "path": "/report", "name": "list_reports", "section": "Reports",
     "doc": "List all reports", "paginated": True},
    {"method": "GET", "path": "/report/{report_id}", "name": "get_report", "section": "Reports",
     "doc": "Get a specific report by ID", "path_params": ["report_id"]},
    # Reviews
    {"method": "GET", "path": "/review", "name": "list_reviews", "section": "Reviews",
     "doc": "List all reviews", "paginated": True},
    {"method": "GET", "path": "/review/{review_id}", "name": "get_review", "section": "Reviews",
     "doc": "Get a specific review by ID", "path_params": ["review_id"]},
    # Objectives
    {"method": "GET", "path": "/objective", "name": "list_objectives", "section": "Objectives",
     "doc": "List all objectives", "paginated": True},
    {"method": "GET", "path": "/objective/{objective_id}", "name": "get_objective", "section": "Objectives",
     "doc": "Get a specific objective by ID", "path_params": ["objective_id"]},
    # Pulse
    {"method": "GET", "path": "/pulse", "name": "list_pulses", "section": "Pulse",
     "doc": "List all pulse surveys", "paginated": True},
    {"method": "GET", "path": "/pulse/{pulse_id}", "name": "get_pulse", "section": "Pulse",
     "doc": "Get a specific pulse survey by ID", "path_params": ["pulse_id"]},
    # Groups
    {"method": "GET", "path": "/group", "name": "list_groups", "section": "Groups",
     "doc": "List all groups", "paginated": True},
    {"method": "GET", "path": "/group/{group_id}", "name": "get_group", "section": "Groups",
     "doc": "Get a specific group by ID", "path_params": ["group_id"]},
    # Departments
    {"method": "GET", "path": "/department", "name": "list_departments", "section": "Departments",
     "doc": "List all departments", "paginated": True},
    {"method": "GET", "path": "/department/{department_id}", "name": "get_department", "section": "Departments",
     "doc": "Get a specific department by ID", "path_params": ["department_id"]},
    # High-Fives
    {"method": "GET", "path": "/high-five", "name": "list_high_fives", "section": "High-Fives",
     "doc": "List all high-fives", "paginated": True},
    {"method": "GET", "path": "/high-five/{high_five_id}", "name": "get_high_five", "section": "High-Fives",
     "doc": "Get a specific high-five by ID", "path_params": ["high_five_id"]},
    # One-on-Ones
    {"method": "GET", "path": "/one-on-one", "name": "list_one_on_ones", "section": "One-on-Ones",
     "doc": "List all one-on-ones", "paginated": True},
    {"method": "GET", "path": "/one-on-one/{one_on_one_id}", "name": "get_one_on_one", "section": "One-on-Ones",
     "doc": "Get a specific one-on-one by ID", "path_params": ["one_on_one_id"]},
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
        sig_parts.append("page_size: int | None = None")

    sig = ",\n        ".join(sig_parts)

    doc_args = ""
    if path_params or paginated:
        doc_args = "\n        Args:\n"
        for p in path_params:
            doc_args += f"            {p}: The {p.replace('_', ' ')}\n"
        if paginated:
            doc_args += "            page: Page number for pagination\n"
            doc_args += "            page_size: Number of items per page\n"

    query_block = ""
    if paginated:
        query_block = """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if page_size is not None:
            query_params['page_size'] = str(page_size)
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
    ) -> FifteenFiveResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            FifteenFiveResponse with operation result
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
            return FifteenFiveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return FifteenFiveResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full 15Five DataSource module code."""
    header = '''# ruff: noqa
"""
15Five REST API DataSource - Auto-generated API wrapper

Generated from 15Five REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.fifteenfive.fifteenfive import FifteenFiveClient, FifteenFiveResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class FifteenFiveDataSource:
    """15Five REST API DataSource

    Provides async wrapper methods for 15Five REST API operations:
    - Users management
    - Reports management
    - Reviews management
    - Objectives management
    - Pulse surveys
    - Groups management
    - Departments management
    - High-fives
    - One-on-ones

    All methods return FifteenFiveResponse objects.
    """

    def __init__(self, client: FifteenFiveClient) -> None:
        """Initialize with FifteenFiveClient.

        Args:
            client: FifteenFiveClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'FifteenFiveDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> FifteenFiveClient:
        """Return the underlying FifteenFiveClient."""
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
