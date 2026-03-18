# ruff: noqa
"""
NICE CXone DataSource Code Generator

This script generates the NiceCXoneDataSource class with all API endpoint
wrapper methods based on the NICE CXone REST API v31.0 specification.

The generated code follows the pattern established by ClickUp and other
connectors in this project, using HTTPRequest/HTTPResponse for all API calls.

Usage:
    python -m app.sources.external.nicecxone.run_generator

Output:
    Prints the generated Python source code for the NiceCXoneDataSource class
    to stdout. Redirect to a file to save:

    python -m app.sources.external.nicecxone.run_generator > \
        app/sources/external/nicecxone/nicecxone.py
"""

from __future__ import annotations

ENDPOINTS = [
    {
        "name": "get_agents",
        "method": "GET",
        "path": "/agents",
        "doc": "Get all agents",
        "path_params": [],
        "query_params": [
            ("updated_since", "str | None", "updatedSince", "Filter agents updated since this date (ISO 8601)"),
            ("skip", "int | None", "skip", "Number of records to skip for pagination"),
            ("top", "int | None", "top", "Number of records to return"),
            ("order_by", "str | None", "orderBy", "Field to order results by"),
            ("is_active", "bool | None", "isActive", "Filter by active status"),
        ],
        "body_params": [],
    },
    {
        "name": "get_agent",
        "method": "GET",
        "path": "/agents/{agent_id}",
        "doc": "Get a specific agent by ID",
        "path_params": [("agent_id", "str", "The agent ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_agent_states",
        "method": "GET",
        "path": "/agents/states",
        "doc": "Get all agent states",
        "path_params": [],
        "query_params": [
            ("updated_since", "str | None", "updatedSince", "Filter states updated since this date (ISO 8601)"),
            ("fields", "str | None", "fields", "Comma-separated list of fields to include"),
        ],
        "body_params": [],
    },
    {
        "name": "get_active_contacts",
        "method": "GET",
        "path": "/contacts/active",
        "doc": "Get all active contacts",
        "path_params": [],
        "query_params": [
            ("updated_since", "str | None", "updatedSince", "Filter contacts updated since this date (ISO 8601)"),
            ("skip", "int | None", "skip", "Number of records to skip for pagination"),
            ("top", "int | None", "top", "Number of records to return"),
        ],
        "body_params": [],
    },
    {
        "name": "get_contact",
        "method": "GET",
        "path": "/contacts/{contact_id}",
        "doc": "Get a specific contact by ID",
        "path_params": [("contact_id", "str", "The contact ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_skills",
        "method": "GET",
        "path": "/skills",
        "doc": "Get all skills",
        "path_params": [],
        "query_params": [
            ("updated_since", "str | None", "updatedSince", "Filter skills updated since this date (ISO 8601)"),
            ("skip", "int | None", "skip", "Number of records to skip for pagination"),
            ("top", "int | None", "top", "Number of records to return"),
            ("order_by", "str | None", "orderBy", "Field to order results by"),
            ("is_active", "bool | None", "isActive", "Filter by active status"),
            ("media_type_id", "int | None", "mediaTypeId", "Filter by media type ID"),
        ],
        "body_params": [],
    },
    {
        "name": "get_skill",
        "method": "GET",
        "path": "/skills/{skill_id}",
        "doc": "Get a specific skill by ID",
        "path_params": [("skill_id", "str", "The skill ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_teams",
        "method": "GET",
        "path": "/teams",
        "doc": "Get all teams",
        "path_params": [],
        "query_params": [
            ("updated_since", "str | None", "updatedSince", "Filter teams updated since this date (ISO 8601)"),
            ("skip", "int | None", "skip", "Number of records to skip for pagination"),
            ("top", "int | None", "top", "Number of records to return"),
            ("order_by", "str | None", "orderBy", "Field to order results by"),
            ("is_active", "bool | None", "isActive", "Filter by active status"),
        ],
        "body_params": [],
    },
    {
        "name": "get_team",
        "method": "GET",
        "path": "/teams/{team_id}",
        "doc": "Get a specific team by ID",
        "path_params": [("team_id", "str", "The team ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_campaigns",
        "method": "GET",
        "path": "/campaigns",
        "doc": "Get all campaigns",
        "path_params": [],
        "query_params": [
            ("updated_since", "str | None", "updatedSince", "Filter campaigns updated since this date (ISO 8601)"),
            ("skip", "int | None", "skip", "Number of records to skip for pagination"),
            ("top", "int | None", "top", "Number of records to return"),
            ("order_by", "str | None", "orderBy", "Field to order results by"),
            ("is_active", "bool | None", "isActive", "Filter by active status"),
        ],
        "body_params": [],
    },
    {
        "name": "get_campaign",
        "method": "GET",
        "path": "/campaigns/{campaign_id}",
        "doc": "Get a specific campaign by ID",
        "path_params": [("campaign_id", "str", "The campaign ID")],
        "query_params": [],
        "body_params": [],
    },
    {
        "name": "get_quality_management_evaluations",
        "method": "GET",
        "path": "/wfo-data/quality-management/evaluations",
        "doc": "Get quality management evaluations",
        "path_params": [],
        "query_params": [
            ("start_date", "str | None", "startDate", "Start date filter (ISO 8601)"),
            ("end_date", "str | None", "endDate", "End date filter (ISO 8601)"),
            ("skip", "int | None", "skip", "Number of records to skip for pagination"),
            ("top", "int | None", "top", "Number of records to return"),
        ],
        "body_params": [],
    },
    {
        "name": "get_contact_history",
        "method": "GET",
        "path": "/reporting/contact-history",
        "doc": "Get contact history report",
        "path_params": [],
        "query_params": [
            ("start_date", "str", "startDate", "Start date for the report (ISO 8601, required)"),
            ("end_date", "str", "endDate", "End date for the report (ISO 8601, required)"),
            ("skip", "int | None", "skip", "Number of records to skip for pagination"),
            ("top", "int | None", "top", "Number of records to return"),
            ("order_by", "str | None", "orderBy", "Field to order results by"),
        ],
        "body_params": [],
    },
    {
        "name": "get_dialing_rules",
        "method": "GET",
        "path": "/dialing-rules",
        "doc": "Get all dialing rules",
        "path_params": [],
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
    body_params = ep.get("body_params", [])

    # Build signature
    sig_parts = ["self"]
    for pp_name, pp_type, _pp_doc in path_params:
        sig_parts.append(f"{pp_name}: {pp_type}")

    # Required query params (no None default)
    required_qp = [q for q in query_params if "None" not in q[1]]
    optional_qp = [q for q in query_params if "None" in q[1]]

    if required_qp or optional_qp or body_params:
        sig_parts.append("*")
    for qp_name, qp_type, _qp_api, _qp_doc in required_qp:
        sig_parts.append(f"{qp_name}: {qp_type}")
    for qp_name, qp_type, _qp_api, _qp_doc in optional_qp:
        sig_parts.append(f"{qp_name}: {qp_type} = None")
    for bp_name, bp_type, _bp_doc in body_params:
        sig_parts.append(f"{bp_name}: {bp_type} = None")

    sig = ",\n        ".join(sig_parts)

    # Build docstring args
    doc_args = []
    for pp_name, _pp_type, pp_doc in path_params:
        doc_args.append(f"            {pp_name}: {pp_doc}")
    for qp_name, _qp_type, _qp_api, qp_doc in query_params:
        doc_args.append(f"            {qp_name}: {qp_doc}")
    for bp_name, _bp_type, bp_doc in body_params:
        doc_args.append(f"            {bp_name}: {bp_doc}")

    args_section = ""
    if doc_args:
        args_section = "\n\n        Args:\n" + "\n".join(doc_args)

    # Build query params block
    qp_block = ""
    if query_params:
        qp_block = "\n        query_params: dict[str, Any] = {}\n"
        for qp_name, qp_type, qp_api, _ in query_params:
            if "None" not in qp_type:
                if "bool" in qp_type:
                    qp_block += f"        query_params['{qp_api}'] = str({qp_name}).lower()\n"
                elif "int" in qp_type:
                    qp_block += f"        query_params['{qp_api}'] = str({qp_name})\n"
                else:
                    qp_block += f"        query_params['{qp_api}'] = {qp_name}\n"
            else:
                if "bool" in qp_type:
                    qp_block += f"        if {qp_name} is not None:\n            query_params['{qp_api}'] = str({qp_name}).lower()\n"
                elif "int" in qp_type:
                    qp_block += f"        if {qp_name} is not None:\n            query_params['{qp_api}'] = str({qp_name})\n"
                else:
                    qp_block += f"        if {qp_name} is not None:\n            query_params['{qp_api}'] = {qp_name}\n"

    # Build URL
    if path_params:
        format_args = ", ".join(f"{p[0]}={p[0]}" for p in path_params)
        url_line = f'        url = self.base_url + "{path}".format({format_args})'
    else:
        url_line = f'        url = self.base_url + "{path}"'

    # Build request kwargs
    req_kwargs = f'method="{method}",\n                url=url,\n                headers={{"Content-Type": "application/json"}}'
    if query_params:
        req_kwargs += ",\n                query=query_params"
    if body_params:
        req_kwargs += ",\n                body=body"

    # Body block
    body_block = ""
    if body_params:
        body_block = "\n        body: dict[str, Any] = {}\n"
        for bp_name, _bp_type, _ in body_params:
            body_block += f"        if {bp_name} is not None:\n            body['{bp_name}'] = {bp_name}\n"

    return f'''    async def {name}(
        {sig}
    ) -> NiceCXoneResponse:
        """{doc}{args_section}

        Returns:
            NiceCXoneResponse with operation result
        """
{qp_block}{url_line}
{body_block}
        try:
            request = HTTPRequest(
                {req_kwargs},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full NiceCXoneDataSource module."""
    header = '''# ruff: noqa
"""
NICE CXone REST API DataSource - Auto-generated API wrapper

Generated from NICE CXone REST API v31.0 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.nicecxone.nicecxone import NiceCXoneClient, NiceCXoneResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class NiceCXoneDataSource:
    """NICE CXone REST API DataSource

    Provides async wrapper methods for NICE CXone REST API operations.
    All methods return NiceCXoneResponse objects.
    """

    def __init__(self, client: NiceCXoneClient) -> None:
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'NiceCXoneDataSource':
        return self

    def get_client(self) -> NiceCXoneClient:
        return self._client

'''
    methods = "\n".join(generate_method(ep) for ep in ENDPOINTS)
    return header + methods


if __name__ == "__main__":
    print(generate_datasource())
