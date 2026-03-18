# ruff: noqa
"""
OneTrust DataSource Code Generator

Defines OneTrust API endpoint specifications and generates the DataSource
wrapper class (onetrust.py) from them.

Endpoints:
  /datasubject/v3/requestqueues, /datasubject/v3/requestqueues/{id},
  /privacynotices/v3/notices, /privacynotices/v3/notices/{id},
  /consent/v1/consentreceipts, /consent/v1/consentreceipts/{id},
  /assessment/v2/assessments, /assessment/v2/assessments/{id},
  /dataInventory/v2/dataElements,
  /riskmanagement/v2/risks, /riskmanagement/v2/risks/{id},
  /vendormanagement/v2/vendors, /vendormanagement/v2/vendors/{id}

Note: For OAuth clients, ensure_token() is called if available.
"""

from __future__ import annotations

ENDPOINTS = [
    # Data Subject Request Queues
    {"method": "GET", "path": "/datasubject/v3/requestqueues", "name": "list_request_queues",
     "section": "Data Subject Requests", "doc": "List all data subject request queues",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/datasubject/v3/requestqueues/{request_id}", "name": "get_request_queue",
     "section": "Data Subject Requests", "doc": "Get a specific data subject request queue by ID",
     "path_params": ["request_id"]},
    # Privacy Notices
    {"method": "GET", "path": "/privacynotices/v3/notices", "name": "list_privacy_notices",
     "section": "Privacy Notices", "doc": "List all privacy notices",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/privacynotices/v3/notices/{notice_id}", "name": "get_privacy_notice",
     "section": "Privacy Notices", "doc": "Get a specific privacy notice by ID",
     "path_params": ["notice_id"]},
    # Consent Receipts
    {"method": "GET", "path": "/consent/v1/consentreceipts", "name": "list_consent_receipts",
     "section": "Consent Receipts", "doc": "List all consent receipts",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/consent/v1/consentreceipts/{receipt_id}", "name": "get_consent_receipt",
     "section": "Consent Receipts", "doc": "Get a specific consent receipt by ID",
     "path_params": ["receipt_id"]},
    # Assessments
    {"method": "GET", "path": "/assessment/v2/assessments", "name": "list_assessments",
     "section": "Assessments", "doc": "List all assessments",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/assessment/v2/assessments/{assessment_id}", "name": "get_assessment",
     "section": "Assessments", "doc": "Get a specific assessment by ID",
     "path_params": ["assessment_id"]},
    # Data Inventory
    {"method": "GET", "path": "/dataInventory/v2/dataElements", "name": "list_data_elements",
     "section": "Data Inventory", "doc": "List all data elements in the data inventory",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    # Risk Management
    {"method": "GET", "path": "/riskmanagement/v2/risks", "name": "list_risks",
     "section": "Risk Management", "doc": "List all risks",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/riskmanagement/v2/risks/{risk_id}", "name": "get_risk",
     "section": "Risk Management", "doc": "Get a specific risk by ID",
     "path_params": ["risk_id"]},
    # Vendor Management
    {"method": "GET", "path": "/vendormanagement/v2/vendors", "name": "list_vendors",
     "section": "Vendor Management", "doc": "List all vendors",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/vendormanagement/v2/vendors/{vendor_id}", "name": "get_vendor",
     "section": "Vendor Management", "doc": "Get a specific vendor by ID",
     "path_params": ["vendor_id"]},
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
    ) -> OneTrustResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            OneTrustResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()
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
            return OneTrustResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return OneTrustResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full OneTrust DataSource module code."""
    header = '''# ruff: noqa
"""
OneTrust REST API DataSource - Auto-generated API wrapper

Generated from OneTrust REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.

Note: For OAuth clients, ensure_token() is called before each request
      to auto-fetch a client_credentials OAuth token.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.onetrust.onetrust import OneTrustClient, OneTrustResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class OneTrustDataSource:
    """OneTrust REST API DataSource

    Provides async wrapper methods for OneTrust REST API operations:
    - Data Subject Requests management
    - Privacy Notices management
    - Consent Receipts management
    - Assessments management
    - Data Inventory management
    - Risk Management
    - Vendor Management

    All methods return OneTrustResponse objects.
    """

    def __init__(self, client: OneTrustClient) -> None:
        """Initialize with OneTrustClient.

        Args:
            client: OneTrustClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'OneTrustDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> OneTrustClient:
        """Return the underlying OneTrustClient."""
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
