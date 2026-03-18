# ruff: noqa
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

    # -----------------------------------------------------------------------
    # Sourcing Projects
    # -----------------------------------------------------------------------

    async def list_sourcing_projects(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> AribaResponse:
        """List all sourcing projects

        HTTP GET /sourcing-projects/v4/prod/sourcing-projects

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            AribaResponse with operation result
        """
        await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/sourcing-projects/v4/prod/sourcing-projects"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_sourcing_projects" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute list_sourcing_projects")


    async def get_sourcing_project(
        self,
        project_id: str
    ) -> AribaResponse:
        """Get a specific sourcing project by ID

        HTTP GET /sourcing-projects/v4/prod/sourcing-projects/{project_id}

        Args:
            project_id: The project id

        Returns:
            AribaResponse with operation result
        """
        await self.http.ensure_token()

        url = self.base_url + "/sourcing-projects/v4/prod/sourcing-projects/{project_id}".format(project_id=project_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_sourcing_project" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute get_sourcing_project")


    # -----------------------------------------------------------------------
    # Purchase Orders
    # -----------------------------------------------------------------------

    async def list_purchase_orders(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> AribaResponse:
        """List all purchase orders

        HTTP GET /procurement/v3/prod/purchase-orders

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            AribaResponse with operation result
        """
        await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/procurement/v3/prod/purchase-orders"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_purchase_orders" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute list_purchase_orders")


    async def get_purchase_order(
        self,
        order_id: str
    ) -> AribaResponse:
        """Get a specific purchase order by ID

        HTTP GET /procurement/v3/prod/purchase-orders/{order_id}

        Args:
            order_id: The order id

        Returns:
            AribaResponse with operation result
        """
        await self.http.ensure_token()

        url = self.base_url + "/procurement/v3/prod/purchase-orders/{order_id}".format(order_id=order_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_purchase_order" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute get_purchase_order")


    # -----------------------------------------------------------------------
    # Invoices
    # -----------------------------------------------------------------------

    async def list_invoices(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> AribaResponse:
        """List all invoices

        HTTP GET /procurement/v3/prod/invoices

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            AribaResponse with operation result
        """
        await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/procurement/v3/prod/invoices"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_invoices" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute list_invoices")


    async def get_invoice(
        self,
        invoice_id: str
    ) -> AribaResponse:
        """Get a specific invoice by ID

        HTTP GET /procurement/v3/prod/invoices/{invoice_id}

        Args:
            invoice_id: The invoice id

        Returns:
            AribaResponse with operation result
        """
        await self.http.ensure_token()

        url = self.base_url + "/procurement/v3/prod/invoices/{invoice_id}".format(invoice_id=invoice_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_invoice" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute get_invoice")


    # -----------------------------------------------------------------------
    # Requisitions
    # -----------------------------------------------------------------------

    async def list_requisitions(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> AribaResponse:
        """List all requisitions

        HTTP GET /procurement/v3/prod/requisitions

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            AribaResponse with operation result
        """
        await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/procurement/v3/prod/requisitions"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_requisitions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute list_requisitions")


    async def get_requisition(
        self,
        requisition_id: str
    ) -> AribaResponse:
        """Get a specific requisition by ID

        HTTP GET /procurement/v3/prod/requisitions/{requisition_id}

        Args:
            requisition_id: The requisition id

        Returns:
            AribaResponse with operation result
        """
        await self.http.ensure_token()

        url = self.base_url + "/procurement/v3/prod/requisitions/{requisition_id}".format(requisition_id=requisition_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_requisition" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute get_requisition")


    # -----------------------------------------------------------------------
    # Suppliers
    # -----------------------------------------------------------------------

    async def list_suppliers(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> AribaResponse:
        """List all suppliers

        HTTP GET /supplier-management/v4/prod/suppliers

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            AribaResponse with operation result
        """
        await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/supplier-management/v4/prod/suppliers"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_suppliers" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute list_suppliers")


    async def get_supplier(
        self,
        supplier_id: str
    ) -> AribaResponse:
        """Get a specific supplier by ID

        HTTP GET /supplier-management/v4/prod/suppliers/{supplier_id}

        Args:
            supplier_id: The supplier id

        Returns:
            AribaResponse with operation result
        """
        await self.http.ensure_token()

        url = self.base_url + "/supplier-management/v4/prod/suppliers/{supplier_id}".format(supplier_id=supplier_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_supplier" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute get_supplier")


    # -----------------------------------------------------------------------
    # Contracts
    # -----------------------------------------------------------------------

    async def list_contracts(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None
    ) -> AribaResponse:
        """List all contracts

        HTTP GET /contract-management/v2/prod/contracts

        Args:
            limit: Maximum number of results
            offset: Offset for pagination

        Returns:
            AribaResponse with operation result
        """
        await self.http.ensure_token()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/contract-management/v2/prod/contracts"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_contracts" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute list_contracts")


    async def get_contract(
        self,
        contract_id: str
    ) -> AribaResponse:
        """Get a specific contract by ID

        HTTP GET /contract-management/v2/prod/contracts/{contract_id}

        Args:
            contract_id: The contract id

        Returns:
            AribaResponse with operation result
        """
        await self.http.ensure_token()

        url = self.base_url + "/contract-management/v2/prod/contracts/{contract_id}".format(contract_id=contract_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AribaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_contract" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AribaResponse(success=False, error=str(e), message="Failed to execute get_contract")

