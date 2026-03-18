"""
NetSuite SuiteTalk REST API DataSource - Auto-generated API wrapper

Generated from NetSuite SuiteTalk REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.netsuite.netsuite import NetSuiteClient, NetSuiteResponse

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class NetSuiteDataSource:
    """NetSuite SuiteTalk REST API DataSource

    Provides async wrapper methods for NetSuite REST API operations:
    - Customer records
    - Sales orders
    - Invoices
    - Items
    - Vendors
    - Employees
    - Contacts
    - Opportunities
    - SuiteQL queries

    The base URL is determined by the NetSuiteClient's configured
    account_id. All methods return NetSuiteResponse objects.
    """

    def __init__(self, client: NetSuiteClient) -> None:
        """Initialize with NetSuiteClient.

        Args:
            client: NetSuiteClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError(
                "HTTP client does not have get_base_url method"
            ) from exc

    def get_data_source(self) -> "NetSuiteDataSource":
        """Return the data source instance."""
        return self

    def get_client(self) -> NetSuiteClient:
        """Return the underlying NetSuiteClient."""
        return self._client

    # ------------------------------------------------------------------
    # Customers
    # ------------------------------------------------------------------

    async def list_customers(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        q: str | None = None,
    ) -> NetSuiteResponse:
        """List customer records

        HTTP GET /record/v1/customer

        Args:
            limit: Maximum number of records to return
            offset: Starting index for pagination
            q: Search query string

        Returns:
            NetSuiteResponse with customer list
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)
        if q is not None:
            query_params["q"] = q

        url = self.base_url + "/record/v1/customer"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_customers"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_customers",
            )

    async def get_customer(
        self,
        customer_id: str,
        *,
        expandSubResources: bool | None = None,
        fields: str | None = None,
    ) -> NetSuiteResponse:
        """Get a customer record by ID

        HTTP GET /record/v1/customer/{id}

        Args:
            customer_id: The customer internal ID
            expandSubResources: Expand sub-resources inline
            fields: Comma-separated list of fields to return

        Returns:
            NetSuiteResponse with customer data
        """
        query_params: dict[str, Any] = {}
        if expandSubResources is not None:
            query_params["expandSubResources"] = str(
                expandSubResources
            ).lower()
        if fields is not None:
            query_params["fields"] = fields

        url = self.base_url + "/record/v1/customer/{customer_id}".format(
            customer_id=customer_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_customer"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_customer",
            )

    # ------------------------------------------------------------------
    # Sales Orders
    # ------------------------------------------------------------------

    async def list_sales_orders(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        q: str | None = None,
    ) -> NetSuiteResponse:
        """List sales order records

        HTTP GET /record/v1/salesOrder

        Args:
            limit: Maximum number of records to return
            offset: Starting index for pagination
            q: Search query string

        Returns:
            NetSuiteResponse with sales order list
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)
        if q is not None:
            query_params["q"] = q

        url = self.base_url + "/record/v1/salesOrder"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_sales_orders"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_sales_orders",
            )

    async def get_sales_order(
        self,
        sales_order_id: str,
        *,
        expandSubResources: bool | None = None,
        fields: str | None = None,
    ) -> NetSuiteResponse:
        """Get a sales order by ID

        HTTP GET /record/v1/salesOrder/{id}

        Args:
            sales_order_id: The sales order internal ID
            expandSubResources: Expand sub-resources inline
            fields: Comma-separated list of fields to return

        Returns:
            NetSuiteResponse with sales order data
        """
        query_params: dict[str, Any] = {}
        if expandSubResources is not None:
            query_params["expandSubResources"] = str(
                expandSubResources
            ).lower()
        if fields is not None:
            query_params["fields"] = fields

        url = self.base_url + "/record/v1/salesOrder/{sales_order_id}".format(
            sales_order_id=sales_order_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_sales_order"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_sales_order",
            )

    # ------------------------------------------------------------------
    # Invoices
    # ------------------------------------------------------------------

    async def list_invoices(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        q: str | None = None,
    ) -> NetSuiteResponse:
        """List invoice records

        HTTP GET /record/v1/invoice

        Args:
            limit: Maximum number of records to return
            offset: Starting index for pagination
            q: Search query string

        Returns:
            NetSuiteResponse with invoice list
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)
        if q is not None:
            query_params["q"] = q

        url = self.base_url + "/record/v1/invoice"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_invoices"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_invoices",
            )

    async def get_invoice(
        self,
        invoice_id: str,
        *,
        expandSubResources: bool | None = None,
        fields: str | None = None,
    ) -> NetSuiteResponse:
        """Get an invoice by ID

        HTTP GET /record/v1/invoice/{id}

        Args:
            invoice_id: The invoice internal ID
            expandSubResources: Expand sub-resources inline
            fields: Comma-separated list of fields to return

        Returns:
            NetSuiteResponse with invoice data
        """
        query_params: dict[str, Any] = {}
        if expandSubResources is not None:
            query_params["expandSubResources"] = str(
                expandSubResources
            ).lower()
        if fields is not None:
            query_params["fields"] = fields

        url = self.base_url + "/record/v1/invoice/{invoice_id}".format(
            invoice_id=invoice_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_invoice"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_invoice",
            )

    # ------------------------------------------------------------------
    # Items
    # ------------------------------------------------------------------

    async def list_items(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        q: str | None = None,
    ) -> NetSuiteResponse:
        """List item records

        HTTP GET /record/v1/item

        Args:
            limit: Maximum number of records to return
            offset: Starting index for pagination
            q: Search query string

        Returns:
            NetSuiteResponse with item list
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)
        if q is not None:
            query_params["q"] = q

        url = self.base_url + "/record/v1/item"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_items"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_items",
            )

    async def get_item(
        self,
        item_id: str,
        *,
        expandSubResources: bool | None = None,
        fields: str | None = None,
    ) -> NetSuiteResponse:
        """Get an item by ID

        HTTP GET /record/v1/item/{id}

        Args:
            item_id: The item internal ID
            expandSubResources: Expand sub-resources inline
            fields: Comma-separated list of fields to return

        Returns:
            NetSuiteResponse with item data
        """
        query_params: dict[str, Any] = {}
        if expandSubResources is not None:
            query_params["expandSubResources"] = str(
                expandSubResources
            ).lower()
        if fields is not None:
            query_params["fields"] = fields

        url = self.base_url + "/record/v1/item/{item_id}".format(
            item_id=item_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_item"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_item",
            )

    # ------------------------------------------------------------------
    # Vendors
    # ------------------------------------------------------------------

    async def list_vendors(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        q: str | None = None,
    ) -> NetSuiteResponse:
        """List vendor records

        HTTP GET /record/v1/vendor

        Args:
            limit: Maximum number of records to return
            offset: Starting index for pagination
            q: Search query string

        Returns:
            NetSuiteResponse with vendor list
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)
        if q is not None:
            query_params["q"] = q

        url = self.base_url + "/record/v1/vendor"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_vendors"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_vendors",
            )

    async def get_vendor(
        self,
        vendor_id: str,
        *,
        expandSubResources: bool | None = None,
        fields: str | None = None,
    ) -> NetSuiteResponse:
        """Get a vendor by ID

        HTTP GET /record/v1/vendor/{id}

        Args:
            vendor_id: The vendor internal ID
            expandSubResources: Expand sub-resources inline
            fields: Comma-separated list of fields to return

        Returns:
            NetSuiteResponse with vendor data
        """
        query_params: dict[str, Any] = {}
        if expandSubResources is not None:
            query_params["expandSubResources"] = str(
                expandSubResources
            ).lower()
        if fields is not None:
            query_params["fields"] = fields

        url = self.base_url + "/record/v1/vendor/{vendor_id}".format(
            vendor_id=vendor_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_vendor"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_vendor",
            )

    # ------------------------------------------------------------------
    # Employees
    # ------------------------------------------------------------------

    async def list_employees(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        q: str | None = None,
    ) -> NetSuiteResponse:
        """List employee records

        HTTP GET /record/v1/employee

        Args:
            limit: Maximum number of records to return
            offset: Starting index for pagination
            q: Search query string

        Returns:
            NetSuiteResponse with employee list
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)
        if q is not None:
            query_params["q"] = q

        url = self.base_url + "/record/v1/employee"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_employees"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_employees",
            )

    async def get_employee(
        self,
        employee_id: str,
        *,
        expandSubResources: bool | None = None,
        fields: str | None = None,
    ) -> NetSuiteResponse:
        """Get an employee by ID

        HTTP GET /record/v1/employee/{id}

        Args:
            employee_id: The employee internal ID
            expandSubResources: Expand sub-resources inline
            fields: Comma-separated list of fields to return

        Returns:
            NetSuiteResponse with employee data
        """
        query_params: dict[str, Any] = {}
        if expandSubResources is not None:
            query_params["expandSubResources"] = str(
                expandSubResources
            ).lower()
        if fields is not None:
            query_params["fields"] = fields

        url = self.base_url + "/record/v1/employee/{employee_id}".format(
            employee_id=employee_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_employee"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_employee",
            )

    # ------------------------------------------------------------------
    # Contacts
    # ------------------------------------------------------------------

    async def list_contacts(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        q: str | None = None,
    ) -> NetSuiteResponse:
        """List contact records

        HTTP GET /record/v1/contact

        Args:
            limit: Maximum number of records to return
            offset: Starting index for pagination
            q: Search query string

        Returns:
            NetSuiteResponse with contact list
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)
        if q is not None:
            query_params["q"] = q

        url = self.base_url + "/record/v1/contact"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_contacts"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_contacts",
            )

    async def get_contact(
        self,
        contact_id: str,
        *,
        expandSubResources: bool | None = None,
        fields: str | None = None,
    ) -> NetSuiteResponse:
        """Get a contact by ID

        HTTP GET /record/v1/contact/{id}

        Args:
            contact_id: The contact internal ID
            expandSubResources: Expand sub-resources inline
            fields: Comma-separated list of fields to return

        Returns:
            NetSuiteResponse with contact data
        """
        query_params: dict[str, Any] = {}
        if expandSubResources is not None:
            query_params["expandSubResources"] = str(
                expandSubResources
            ).lower()
        if fields is not None:
            query_params["fields"] = fields

        url = self.base_url + "/record/v1/contact/{contact_id}".format(
            contact_id=contact_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_contact"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_contact",
            )

    # ------------------------------------------------------------------
    # Opportunities
    # ------------------------------------------------------------------

    async def list_opportunities(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        q: str | None = None,
    ) -> NetSuiteResponse:
        """List opportunity records

        HTTP GET /record/v1/opportunity

        Args:
            limit: Maximum number of records to return
            offset: Starting index for pagination
            q: Search query string

        Returns:
            NetSuiteResponse with opportunity list
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)
        if q is not None:
            query_params["q"] = q

        url = self.base_url + "/record/v1/opportunity"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_opportunities"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_opportunities",
            )

    async def get_opportunity(
        self,
        opportunity_id: str,
        *,
        expandSubResources: bool | None = None,
        fields: str | None = None,
    ) -> NetSuiteResponse:
        """Get an opportunity by ID

        HTTP GET /record/v1/opportunity/{id}

        Args:
            opportunity_id: The opportunity internal ID
            expandSubResources: Expand sub-resources inline
            fields: Comma-separated list of fields to return

        Returns:
            NetSuiteResponse with opportunity data
        """
        query_params: dict[str, Any] = {}
        if expandSubResources is not None:
            query_params["expandSubResources"] = str(
                expandSubResources
            ).lower()
        if fields is not None:
            query_params["fields"] = fields

        url = self.base_url + "/record/v1/opportunity/{opportunity_id}".format(
            opportunity_id=opportunity_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_opportunity"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_opportunity",
            )

    # ------------------------------------------------------------------
    # SuiteQL
    # ------------------------------------------------------------------

    async def execute_suiteql(
        self,
        query: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> NetSuiteResponse:
        """Execute a SuiteQL query

        HTTP POST /query/v1/suiteql

        Args:
            query: The SuiteQL query string
            limit: Maximum number of records to return
            offset: Starting index for pagination

        Returns:
            NetSuiteResponse with query results
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)

        url = self.base_url + "/query/v1/suiteql"

        body: dict[str, Any] = {"q": query}

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Prefer": "transient",
                },
                query=query_params,
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NetSuiteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed execute_suiteql"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return NetSuiteResponse(
                success=False,
                error=str(e),
                message="Failed to execute execute_suiteql",
            )
