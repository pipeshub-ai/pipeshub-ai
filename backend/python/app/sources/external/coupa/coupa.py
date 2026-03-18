# ruff: noqa
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

    # -----------------------------------------------------------------------
    # Purchase Orders
    # -----------------------------------------------------------------------

    async def list_purchase_orders(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> CoupaResponse:
        """List all purchase orders

        HTTP GET /purchase_orders

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/purchase_orders"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_purchase_orders" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute list_purchase_orders")


    async def get_purchase_order(
        self,
        order_id: str
    ) -> CoupaResponse:
        """Get a specific purchase order by ID

        HTTP GET /purchase_orders/{order_id}

        Args:
            order_id: The order id

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/purchase_orders/{order_id}".format(order_id=order_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_purchase_order" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute get_purchase_order")


    # -----------------------------------------------------------------------
    # Invoices
    # -----------------------------------------------------------------------

    async def list_invoices(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> CoupaResponse:
        """List all invoices

        HTTP GET /invoices

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/invoices"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_invoices" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute list_invoices")


    async def get_invoice(
        self,
        invoice_id: str
    ) -> CoupaResponse:
        """Get a specific invoice by ID

        HTTP GET /invoices/{invoice_id}

        Args:
            invoice_id: The invoice id

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/invoices/{invoice_id}".format(invoice_id=invoice_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_invoice" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute get_invoice")


    # -----------------------------------------------------------------------
    # Requisitions
    # -----------------------------------------------------------------------

    async def list_requisitions(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> CoupaResponse:
        """List all requisitions

        HTTP GET /requisitions

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/requisitions"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_requisitions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute list_requisitions")


    async def get_requisition(
        self,
        requisition_id: str
    ) -> CoupaResponse:
        """Get a specific requisition by ID

        HTTP GET /requisitions/{requisition_id}

        Args:
            requisition_id: The requisition id

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/requisitions/{requisition_id}".format(requisition_id=requisition_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_requisition" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute get_requisition")


    # -----------------------------------------------------------------------
    # Suppliers
    # -----------------------------------------------------------------------

    async def list_suppliers(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> CoupaResponse:
        """List all suppliers

        HTTP GET /suppliers

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/suppliers"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_suppliers" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute list_suppliers")


    async def get_supplier(
        self,
        supplier_id: str
    ) -> CoupaResponse:
        """Get a specific supplier by ID

        HTTP GET /suppliers/{supplier_id}

        Args:
            supplier_id: The supplier id

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/suppliers/{supplier_id}".format(supplier_id=supplier_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_supplier" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute get_supplier")


    # -----------------------------------------------------------------------
    # Contracts
    # -----------------------------------------------------------------------

    async def list_contracts(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> CoupaResponse:
        """List all contracts

        HTTP GET /contracts

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/contracts"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_contracts" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute list_contracts")


    async def get_contract(
        self,
        contract_id: str
    ) -> CoupaResponse:
        """Get a specific contract by ID

        HTTP GET /contracts/{contract_id}

        Args:
            contract_id: The contract id

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/contracts/{contract_id}".format(contract_id=contract_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_contract" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute get_contract")


    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def list_users(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> CoupaResponse:
        """List all users

        HTTP GET /users

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/users"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute list_users")


    async def get_user(
        self,
        user_id: str
    ) -> CoupaResponse:
        """Get a specific user by ID

        HTTP GET /users/{user_id}

        Args:
            user_id: The user id

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/users/{user_id}".format(user_id=user_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute get_user")


    # -----------------------------------------------------------------------
    # Departments
    # -----------------------------------------------------------------------

    async def list_departments(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> CoupaResponse:
        """List all departments

        HTTP GET /departments

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/departments"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_departments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute list_departments")


    async def get_department(
        self,
        department_id: str
    ) -> CoupaResponse:
        """Get a specific department by ID

        HTTP GET /departments/{department_id}

        Args:
            department_id: The department id

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/departments/{department_id}".format(department_id=department_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_department" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute get_department")


    # -----------------------------------------------------------------------
    # Expense Reports
    # -----------------------------------------------------------------------

    async def list_expense_reports(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> CoupaResponse:
        """List all expense reports

        HTTP GET /expense_reports

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/expense_reports"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_expense_reports" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute list_expense_reports")


    async def get_expense_report(
        self,
        report_id: str
    ) -> CoupaResponse:
        """Get a specific expense report by ID

        HTTP GET /expense_reports/{report_id}

        Args:
            report_id: The report id

        Returns:
            CoupaResponse with operation result
        """
        if hasattr(self.http, 'ensure_token'):
            await self.http.ensure_token()

        url = self.base_url + "/expense_reports/{report_id}".format(report_id=report_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CoupaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_expense_report" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CoupaResponse(success=False, error=str(e), message="Failed to execute get_expense_report")

