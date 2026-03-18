# ruff: noqa
"""
SAP Ariba DataSource Code Generator

Defines SAP Ariba API endpoint specifications and generates the DataSource
wrapper class (ariba.py) from them.

Endpoints:
  /sourcing-projects/v4/prod/sourcing-projects,
  /sourcing-projects/v4/prod/sourcing-projects/{id},
  /procurement/v3/prod/purchase-orders,
  /procurement/v3/prod/purchase-orders/{id},
  /procurement/v3/prod/invoices, /procurement/v3/prod/invoices/{id},
  /procurement/v3/prod/requisitions, /procurement/v3/prod/requisitions/{id},
  /supplier-management/v4/prod/suppliers,
  /supplier-management/v4/prod/suppliers/{id},
  /contract-management/v2/prod/contracts,
  /contract-management/v2/prod/contracts/{id}

Note: The Ariba client uses client_credentials OAuth with auto token fetch.
      The DataSource calls ensure_token() before each request.
"""

from __future__ import annotations

ENDPOINTS = [
    # Sourcing Projects
    {"method": "GET", "path": "/sourcing-projects/v4/prod/sourcing-projects", "name": "list_sourcing_projects",
     "section": "Sourcing Projects", "doc": "List all sourcing projects",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/sourcing-projects/v4/prod/sourcing-projects/{project_id}", "name": "get_sourcing_project",
     "section": "Sourcing Projects", "doc": "Get a specific sourcing project by ID", "path_params": ["project_id"]},
    # Purchase Orders
    {"method": "GET", "path": "/procurement/v3/prod/purchase-orders", "name": "list_purchase_orders",
     "section": "Purchase Orders", "doc": "List all purchase orders",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/procurement/v3/prod/purchase-orders/{order_id}", "name": "get_purchase_order",
     "section": "Purchase Orders", "doc": "Get a specific purchase order by ID", "path_params": ["order_id"]},
    # Invoices
    {"method": "GET", "path": "/procurement/v3/prod/invoices", "name": "list_invoices",
     "section": "Invoices", "doc": "List all invoices",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/procurement/v3/prod/invoices/{invoice_id}", "name": "get_invoice",
     "section": "Invoices", "doc": "Get a specific invoice by ID", "path_params": ["invoice_id"]},
    # Requisitions
    {"method": "GET", "path": "/procurement/v3/prod/requisitions", "name": "list_requisitions",
     "section": "Requisitions", "doc": "List all requisitions",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/procurement/v3/prod/requisitions/{requisition_id}", "name": "get_requisition",
     "section": "Requisitions", "doc": "Get a specific requisition by ID", "path_params": ["requisition_id"]},
    # Suppliers
    {"method": "GET", "path": "/supplier-management/v4/prod/suppliers", "name": "list_suppliers",
     "section": "Suppliers", "doc": "List all suppliers",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/supplier-management/v4/prod/suppliers/{supplier_id}", "name": "get_supplier",
     "section": "Suppliers", "doc": "Get a specific supplier by ID", "path_params": ["supplier_id"]},
    # Contracts
    {"method": "GET", "path": "/contract-management/v2/prod/contracts", "name": "list_contracts",
     "section": "Contracts", "doc": "List all contracts",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/contract-management/v2/prod/contracts/{contract_id}", "name": "get_contract",
     "section": "Contracts", "doc": "Get a specific contract by ID", "path_params": ["contract_id"]},
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
    ) -> AribaResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            AribaResponse with operation result
        """
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
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full SAP Ariba DataSource module code."""
    header = '''# ruff: noqa
"""
SAP Ariba REST API DataSource - Auto-generated API wrapper

Generated from SAP Ariba REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.

Note: Each method calls ensure_token() to auto-fetch a client_credentials
      OAuth token before making the API request.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.ariba.ariba import AribaClient, AribaResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class AribaDataSource:
    """SAP Ariba REST API DataSource

    Provides async wrapper methods for SAP Ariba REST API operations:
    - Sourcing Projects management
    - Purchase Orders management
    - Invoices management
    - Requisitions management
    - Suppliers management
    - Contracts management

    All methods return AribaResponse objects.
    Token is automatically fetched via client_credentials OAuth.
    """

    def __init__(self, client: AribaClient) -> None:
        """Initialize with AribaClient.

        Args:
            client: AribaClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'AribaDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> AribaClient:
        """Return the underlying AribaClient."""
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
