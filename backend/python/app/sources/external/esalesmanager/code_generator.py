# ruff: noqa
"""
eSalesManager DataSource Code Generator

Defines eSalesManager API endpoint specifications and generates the DataSource
wrapper class (esalesmanager.py) from them.

Endpoints:
  /customers, /customers/{id}, /contacts, /contacts/{id},
  /activities, /activities/{id}, /deals, /deals/{id},
  /products, /products/{id}, /tasks, /tasks/{id},
  /reports, /users, /users/{id}
"""

from __future__ import annotations

ENDPOINTS = [
    # Customers
    {"method": "GET", "path": "/customers", "name": "list_customers", "section": "Customers",
     "doc": "List all customers", "paginated": True},
    {"method": "GET", "path": "/customers/{customer_id}", "name": "get_customer", "section": "Customers",
     "doc": "Get a specific customer by ID", "path_params": ["customer_id"]},
    # Contacts
    {"method": "GET", "path": "/contacts", "name": "list_contacts", "section": "Contacts",
     "doc": "List all contacts", "paginated": True},
    {"method": "GET", "path": "/contacts/{contact_id}", "name": "get_contact", "section": "Contacts",
     "doc": "Get a specific contact by ID", "path_params": ["contact_id"]},
    # Activities
    {"method": "GET", "path": "/activities", "name": "list_activities", "section": "Activities",
     "doc": "List all activities", "paginated": True},
    {"method": "GET", "path": "/activities/{activity_id}", "name": "get_activity", "section": "Activities",
     "doc": "Get a specific activity by ID", "path_params": ["activity_id"]},
    # Deals
    {"method": "GET", "path": "/deals", "name": "list_deals", "section": "Deals",
     "doc": "List all deals", "paginated": True},
    {"method": "GET", "path": "/deals/{deal_id}", "name": "get_deal", "section": "Deals",
     "doc": "Get a specific deal by ID", "path_params": ["deal_id"]},
    # Products
    {"method": "GET", "path": "/products", "name": "list_products", "section": "Products",
     "doc": "List all products", "paginated": True},
    {"method": "GET", "path": "/products/{product_id}", "name": "get_product", "section": "Products",
     "doc": "Get a specific product by ID", "path_params": ["product_id"]},
    # Tasks
    {"method": "GET", "path": "/tasks", "name": "list_tasks", "section": "Tasks",
     "doc": "List all tasks", "paginated": True},
    {"method": "GET", "path": "/tasks/{task_id}", "name": "get_task", "section": "Tasks",
     "doc": "Get a specific task by ID", "path_params": ["task_id"]},
    # Reports
    {"method": "GET", "path": "/reports", "name": "list_reports", "section": "Reports",
     "doc": "List all reports", "paginated": True},
    # Users
    {"method": "GET", "path": "/users", "name": "list_users", "section": "Users",
     "doc": "List all users", "paginated": True},
    {"method": "GET", "path": "/users/{user_id}", "name": "get_user", "section": "Users",
     "doc": "Get a specific user by ID", "path_params": ["user_id"]},
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
        sig_parts.append("per_page: int | None = None")

    sig = ",\n        ".join(sig_parts)

    doc_args = ""
    if path_params or paginated:
        doc_args = "\n        Args:\n"
        for p in path_params:
            doc_args += f"            {p}: The {p.replace('_', ' ')}\n"
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

    req_extra = ""
    if paginated:
        req_extra = "\n                query=query_params,"

    return f'''
    async def {name}(
        {sig}
    ) -> ESalesManagerResponse:
        """{doc}

        HTTP {method} {path}
{doc_args}
        Returns:
            ESalesManagerResponse with operation result
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
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed {name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute {name}")
'''


def generate_datasource() -> str:
    """Generate the full eSalesManager DataSource module code."""
    header = '''# ruff: noqa
"""
eSalesManager REST API DataSource - Auto-generated API wrapper

Generated from eSalesManager REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.esalesmanager.esalesmanager import ESalesManagerClient, ESalesManagerResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class ESalesManagerDataSource:
    """eSalesManager REST API DataSource

    Provides async wrapper methods for eSalesManager REST API operations:
    - Customers management
    - Contacts management
    - Activities management
    - Deals management
    - Products management
    - Tasks management
    - Reports
    - Users management

    All methods return ESalesManagerResponse objects.
    """

    def __init__(self, client: ESalesManagerClient) -> None:
        """Initialize with ESalesManagerClient.

        Args:
            client: ESalesManagerClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'ESalesManagerDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> ESalesManagerClient:
        """Return the underlying ESalesManagerClient."""
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
