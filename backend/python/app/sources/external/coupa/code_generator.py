# ruff: noqa
"""
Coupa DataSource Code Generator

Defines Coupa API endpoint specifications and generates the DataSource
wrapper class (coupa.py) from them.

Endpoints:
  /purchase_orders, /purchase_orders/{id}, /invoices, /invoices/{id},
  /requisitions, /requisitions/{id}, /suppliers, /suppliers/{id},
  /contracts, /contracts/{id}, /users, /users/{id},
  /departments, /departments/{id}, /expense_reports, /expense_reports/{id}

Note: For OAuth clients, ensure_token() is called if available.
"""

from __future__ import annotations

ENDPOINTS = [
    # Purchase Orders
    {"method": "GET", "path": "/purchase_orders", "name": "list_purchase_orders",
     "section": "Purchase Orders", "doc": "List all purchase orders",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/purchase_orders/{order_id}", "name": "get_purchase_order",
     "section": "Purchase Orders", "doc": "Get a specific purchase order by ID", "path_params": ["order_id"]},
    # Invoices
    {"method": "GET", "path": "/invoices", "name": "list_invoices",
     "section": "Invoices", "doc": "List all invoices",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/invoices/{invoice_id}", "name": "get_invoice",
     "section": "Invoices", "doc": "Get a specific invoice by ID", "path_params": ["invoice_id"]},
    # Requisitions
    {"method": "GET", "path": "/requisitions", "name": "list_requisitions",
     "section": "Requisitions", "doc": "List all requisitions",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/requisitions/{requisition_id}", "name": "get_requisition",
     "section": "Requisitions", "doc": "Get a specific requisition by ID", "path_params": ["requisition_id"]},
    # Suppliers
    {"method": "GET", "path": "/suppliers", "name": "list_suppliers",
     "section": "Suppliers", "doc": "List all suppliers",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/suppliers/{supplier_id}", "name": "get_supplier",
     "section": "Suppliers", "doc": "Get a specific supplier by ID", "path_params": ["supplier_id"]},
    # Contracts
    {"method": "GET", "path": "/contracts", "name": "list_contracts",
     "section": "Contracts", "doc": "List all contracts",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/contracts/{contract_id}", "name": "get_contract",
     "section": "Contracts", "doc": "Get a specific contract by ID", "path_params": ["contract_id"]},
    # Users
    {"method": "GET", "path": "/users", "name": "list_users",
     "section": "Users", "doc": "List all users",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/users/{user_id}", "name": "get_user",
     "section": "Users", "doc": "Get a specific user by ID", "path_params": ["user_id"]},
    # Departments
    {"method": "GET", "path": "/departments", "name": "list_departments",
     "section": "Departments", "doc": "List all departments",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/departments/{department_id}", "name": "get_department",
     "section": "Departments", "doc": "Get a specific department by ID", "path_params": ["department_id"]},
    # Expense Reports
    {"method": "GET", "path": "/expense_reports", "name": "list_expense_reports",
     "section": "Expense Reports", "doc": "List all expense reports",
     "query_params": [("limit", "int", "Maximum number of results"), ("offset", "int", "Offset for pagination")]},
    {"method": "GET", "path": "/expense_reports/{report_id}", "name": "get_expense_report",
     "section": "Expense Reports", "doc": "Get a specific expense report by ID", "path_params": ["report_id"]},
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
    ) -> CoupaResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            CoupaResponse with operation result
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
                headers={{"Content-Type": "application/json", "Accept": "application/json"}},{req_extra}
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full Coupa DataSource module code."""
    header = '''# ruff: noqa
"""
Coupa REST API DataSource - Auto-generated API wrapper

Generated from Coupa REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.

Note: For OAuth clients, ensure_token() is called before each request
      to auto-fetch a client_credentials OAuth token.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.coupa.coupa import CoupaClient, CoupaResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class CoupaDataSource:
    """Coupa REST API DataSource

    Provides async wrapper methods for Coupa REST API operations:
    - Purchase Orders management
    - Invoices management
    - Requisitions management
    - Suppliers management
    - Contracts management
    - Users management
    - Departments management
    - Expense Reports management

    All methods return CoupaResponse objects.
    """

    def __init__(self, client: CoupaClient) -> None:
        """Initialize with CoupaClient.

        Args:
            client: CoupaClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'CoupaDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> CoupaClient:
        """Return the underlying CoupaClient."""
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
