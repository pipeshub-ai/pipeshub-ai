# ruff: noqa
"""
Haystack DataSource Code Generator

Defines Haystack API endpoint specifications and generates the DataSource
wrapper class (haystack.py) from them.

Endpoints:
  /people, /people/{id}, /teams, /teams/{id}, /locations, /locations/{id},
  /departments, /departments/{id}, /announcements, /announcements/{id},
  /pages, /pages/{id}, /search
"""

from __future__ import annotations

ENDPOINTS = [
    # People
    {"method": "GET", "path": "/people", "name": "get_people", "section": "People",
     "doc": "List all people", "paginated": True},
    {"method": "GET", "path": "/people/{person_id}", "name": "get_person", "section": "People",
     "doc": "Get a specific person by ID", "path_params": ["person_id"]},
    # Teams
    {"method": "GET", "path": "/teams", "name": "get_teams", "section": "Teams",
     "doc": "List all teams", "paginated": True},
    {"method": "GET", "path": "/teams/{team_id}", "name": "get_team", "section": "Teams",
     "doc": "Get a specific team by ID", "path_params": ["team_id"]},
    # Locations
    {"method": "GET", "path": "/locations", "name": "get_locations", "section": "Locations",
     "doc": "List all locations", "paginated": True},
    {"method": "GET", "path": "/locations/{location_id}", "name": "get_location", "section": "Locations",
     "doc": "Get a specific location by ID", "path_params": ["location_id"]},
    # Departments
    {"method": "GET", "path": "/departments", "name": "get_departments", "section": "Departments",
     "doc": "List all departments", "paginated": True},
    {"method": "GET", "path": "/departments/{department_id}", "name": "get_department", "section": "Departments",
     "doc": "Get a specific department by ID", "path_params": ["department_id"]},
    # Announcements
    {"method": "GET", "path": "/announcements", "name": "get_announcements", "section": "Announcements",
     "doc": "List all announcements", "paginated": True},
    {"method": "GET", "path": "/announcements/{announcement_id}", "name": "get_announcement", "section": "Announcements",
     "doc": "Get a specific announcement by ID", "path_params": ["announcement_id"]},
    # Pages
    {"method": "GET", "path": "/pages", "name": "get_pages", "section": "Pages",
     "doc": "List all pages", "paginated": True},
    {"method": "GET", "path": "/pages/{page_id}", "name": "get_page", "section": "Pages",
     "doc": "Get a specific page by ID", "path_params": ["page_id"]},
    # Search
    {"method": "GET", "path": "/search", "name": "search", "section": "Search",
     "doc": "Search across Haystack content",
     "query_params": [("q", "q", "str", "Search query string")],
     "extra_query": [("type", "type", "str | None", "Filter by content type", True),
                     ("limit", "limit", "int | None", "Maximum number of results to return", True)]},
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
    query_params = ep.get("query_params", [])
    extra_query = ep.get("extra_query", [])
    has_query = paginated or query_params or extra_query

    sig_parts = ["self"]
    for p in path_params:
        sig_parts.append(f"{p}: str")
    for qp in query_params:
        sig_parts.append(f"{qp[0]}: {qp[2]}")
    for bp in body_params:
        sig_parts.append(f"{bp[0]}: {bp[2]}")
    if paginated or extra_query:
        sig_parts.append("*")
        if paginated:
            sig_parts.append("limit: int | None = None")
            sig_parts.append("offset: int | None = None")
        for eq in extra_query:
            sig_parts.append(f"{eq[0]}: {eq[2]} = None")

    sig = ",\n        ".join(sig_parts)

    doc_args = ""
    if path_params or paginated or body_params or query_params or extra_query:
        doc_args = "\n        Args:\n"
        for p in path_params:
            doc_args += f"            {p}: The {p.replace('_', ' ')}\n"
        for qp in query_params:
            doc_args += f"            {qp[0]}: {qp[3]}\n"
        for bp in body_params:
            doc_args += f"            {bp[0]}: {bp[3]}\n"
        if paginated:
            doc_args += "            limit: Maximum number of results to return\n"
            doc_args += "            offset: Number of results to skip\n"
        for eq in extra_query:
            doc_args += f"            {eq[0]}: {eq[3]}\n"

    query_block = ""
    if has_query:
        lines = ["", "        query_params: dict[str, Any] = {}"]
        for qp in query_params:
            lines.append(f"        query_params['{qp[1]}'] = {qp[0]}")
        if paginated:
            lines.append("        if limit is not None:")
            lines.append("            query_params['limit'] = str(limit)")
            lines.append("        if offset is not None:")
            lines.append("            query_params['offset'] = str(offset)")
        for eq in extra_query:
            lines.append(f"        if {eq[0]} is not None:")
            if "int" in eq[2]:
                lines.append(f"            query_params['{eq[1]}'] = str({eq[0]})")
            else:
                lines.append(f"            query_params['{eq[1]}'] = {eq[0]}")
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
    if has_query:
        req_extra += "\n                query=query_params,"
    if body_params:
        req_extra += "\n                body=body,"

    return f'''
    async def {name}(
        {sig},
    ) -> HaystackResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            HaystackResponse with operation result
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
            return HaystackResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return HaystackResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full Haystack DataSource module code."""
    header = '''# ruff: noqa
"""
Haystack REST API DataSource - Auto-generated API wrapper

Generated from Haystack REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.haystack.haystack import HaystackClient, HaystackResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class HaystackDataSource:
    """Haystack REST API DataSource

    Provides async wrapper methods for Haystack REST API operations:
    - People management
    - Teams management
    - Locations management
    - Departments management
    - Announcements management
    - Pages management
    - Search

    The base URL is https://api.haystackapp.io/v1.

    All methods return HaystackResponse objects.
    """

    def __init__(self, client: HaystackClient) -> None:
        """Initialize with HaystackClient.

        Args:
            client: HaystackClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'HaystackDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> HaystackClient:
        """Return the underlying HaystackClient."""
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
