# ruff: noqa
"""
Guru DataSource Code Generator

Defines Guru API endpoint specifications and generates the DataSource
wrapper class (guru.py) from them.

Endpoints:
  /cards, /cards/{id}, /cards/{id}/extended, /boards, /boards/{id},
  /boards/{id}/items, /collections, /collections/{id}, /groups,
  /groups/{id}, /members, /search/cardmgr (POST), /teams/teaminfo,
  /analytics/card
"""

from __future__ import annotations

ENDPOINTS = [
    # Cards
    {"method": "GET", "path": "/cards", "name": "list_cards", "section": "Cards",
     "doc": "List all cards", "paginated": True},
    {"method": "GET", "path": "/cards/{card_id}", "name": "get_card", "section": "Cards",
     "doc": "Get a specific card by ID", "path_params": ["card_id"]},
    {"method": "GET", "path": "/cards/{card_id}/extended", "name": "get_card_extended", "section": "Cards",
     "doc": "Get extended card details by ID", "path_params": ["card_id"]},
    # Boards
    {"method": "GET", "path": "/boards", "name": "list_boards", "section": "Boards",
     "doc": "List all boards"},
    {"method": "GET", "path": "/boards/{board_id}", "name": "get_board", "section": "Boards",
     "doc": "Get a specific board by ID", "path_params": ["board_id"]},
    {"method": "GET", "path": "/boards/{board_id}/items", "name": "get_board_items", "section": "Boards",
     "doc": "Get items on a specific board", "path_params": ["board_id"]},
    # Collections
    {"method": "GET", "path": "/collections", "name": "list_collections", "section": "Collections",
     "doc": "List all collections"},
    {"method": "GET", "path": "/collections/{collection_id}", "name": "get_collection", "section": "Collections",
     "doc": "Get a specific collection by ID", "path_params": ["collection_id"]},
    # Groups
    {"method": "GET", "path": "/groups", "name": "list_groups", "section": "Groups",
     "doc": "List all groups"},
    {"method": "GET", "path": "/groups/{group_id}", "name": "get_group", "section": "Groups",
     "doc": "Get a specific group by ID", "path_params": ["group_id"]},
    # Members
    {"method": "GET", "path": "/members", "name": "list_members", "section": "Members",
     "doc": "List all members"},
    # Search
    {"method": "POST", "path": "/search/cardmgr", "name": "search_cards", "section": "Search",
     "doc": "Search cards using the card manager search",
     "body_params": [("search_terms", "searchTerms", "str", "Search query string")]},
    # Team Info
    {"method": "GET", "path": "/teams/teaminfo", "name": "get_team_info", "section": "Team Info",
     "doc": "Get team information"},
    # Analytics
    {"method": "GET", "path": "/analytics/card", "name": "get_card_analytics", "section": "Analytics",
     "doc": "Get card analytics"},
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
        sig_parts.append("page: int | None = None")
        sig_parts.append("per_page: int | None = None")

    sig = ",\n        ".join(sig_parts)

    doc_args = ""
    if path_params or paginated or body_params:
        doc_args = "\n        Args:\n"
        for p in path_params:
            doc_args += f"            {p}: The {p.replace('_', ' ')}\n"
        for bp in body_params:
            doc_args += f"            {bp[0]}: {bp[3]}\n"
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
        {sig}
    ) -> GuruResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            GuruResponse with operation result
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
            return GuruResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return GuruResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full Guru DataSource module code."""
    header = '''# ruff: noqa
"""
Guru REST API DataSource - Auto-generated API wrapper

Generated from Guru REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.guru.guru import GuruClient, GuruResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class GuruDataSource:
    """Guru REST API DataSource

    Provides async wrapper methods for Guru REST API operations:
    - Cards management
    - Boards management
    - Collections management
    - Groups management
    - Members management
    - Search
    - Team info
    - Analytics

    All methods return GuruResponse objects.
    """

    def __init__(self, client: GuruClient) -> None:
        """Initialize with GuruClient.

        Args:
            client: GuruClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'GuruDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> GuruClient:
        """Return the underlying GuruClient."""
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
