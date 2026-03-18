"""
QuickBooks Online REST API DataSource - Auto-generated API wrapper

Generated from QuickBooks Online REST API v3 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.quickbooks.quickbooks import (
    QuickBooksClient,
    QuickBooksResponse,
)

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class QuickBooksDataSource:
    """QuickBooks Online REST API DataSource

    Provides async wrapper methods for QuickBooks Online REST API v3 operations:
    - SQL-like query endpoint
    - Customer CRUD
    - Invoice CRUD
    - Payment CRUD
    - Vendor CRUD
    - Item CRUD
    - Account CRUD
    - Bill CRUD
    - Estimate CRUD
    - Employee CRUD
    - Company info

    The base URL includes the company_id as configured in the client.
    All methods return QuickBooksResponse objects.
    """

    def __init__(self, client: QuickBooksClient) -> None:
        """Initialize with QuickBooksClient.

        Args:
            client: QuickBooksClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError(
                "HTTP client does not have get_base_url method"
            ) from exc

    def get_data_source(self) -> "QuickBooksDataSource":
        """Return the data source instance."""
        return self

    def get_client(self) -> QuickBooksClient:
        """Return the underlying QuickBooksClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Query (SQL-like)
    # -----------------------------------------------------------------------

    async def query(self, query_string: str) -> QuickBooksResponse:
        """Execute a SQL-like query against QuickBooks data.

        Example queries:
            "SELECT * FROM Customer"
            "SELECT * FROM Invoice WHERE TotalAmt > '100.00'"
            "SELECT * FROM Item STARTPOSITION 1 MAXRESULTS 10"

        Args:
            query_string: SQL-like query string

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/query"
        query_params: dict[str, Any] = {"query": query_string}

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed query"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute query",
            )

    # -----------------------------------------------------------------------
    # Customer
    # -----------------------------------------------------------------------

    async def get_customer(self, customer_id: str) -> QuickBooksResponse:
        """Get a customer by ID.

        Args:
            customer_id: The customer ID

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/customer/{customer_id}".format(
            customer_id=customer_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_customer"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_customer",
            )

    async def create_customer(
        self,
        display_name: str,
        *,
        given_name: str | None = None,
        family_name: str | None = None,
        company_name: str | None = None,
        primary_email: str | None = None,
        primary_phone: str | None = None,
    ) -> QuickBooksResponse:
        """Create a new customer.

        Args:
            display_name: Customer display name (required)
            given_name: Customer first name
            family_name: Customer last name
            company_name: Company name
            primary_email: Primary email address
            primary_phone: Primary phone number

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/customer"

        body: dict[str, Any] = {"DisplayName": display_name}
        if given_name is not None:
            body["GivenName"] = given_name
        if family_name is not None:
            body["FamilyName"] = family_name
        if company_name is not None:
            body["CompanyName"] = company_name
        if primary_email is not None:
            body["PrimaryEmailAddr"] = {"Address": primary_email}
        if primary_phone is not None:
            body["PrimaryPhone"] = {"FreeFormNumber": primary_phone}

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed create_customer"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute create_customer",
            )

    # -----------------------------------------------------------------------
    # Invoice
    # -----------------------------------------------------------------------

    async def get_invoice(self, invoice_id: str) -> QuickBooksResponse:
        """Get an invoice by ID.

        Args:
            invoice_id: The invoice ID

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/invoice/{invoice_id}".format(
            invoice_id=invoice_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_invoice"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_invoice",
            )

    async def create_invoice(
        self,
        customer_ref_value: str,
        *,
        line_items: list[dict[str, Any]] | None = None,
        due_date: str | None = None,
    ) -> QuickBooksResponse:
        """Create a new invoice.

        Args:
            customer_ref_value: Customer reference ID (required)
            line_items: List of line item dicts (Amount, DetailType, etc.)
            due_date: Due date in YYYY-MM-DD format

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/invoice"

        body: dict[str, Any] = {
            "CustomerRef": {"value": customer_ref_value}
        }
        if line_items is not None:
            body["Line"] = line_items
        if due_date is not None:
            body["DueDate"] = due_date

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed create_invoice"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute create_invoice",
            )

    # -----------------------------------------------------------------------
    # Payment
    # -----------------------------------------------------------------------

    async def get_payment(self, payment_id: str) -> QuickBooksResponse:
        """Get a payment by ID.

        Args:
            payment_id: The payment ID

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/payment/{payment_id}".format(
            payment_id=payment_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_payment"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_payment",
            )

    # -----------------------------------------------------------------------
    # Vendor
    # -----------------------------------------------------------------------

    async def get_vendor(self, vendor_id: str) -> QuickBooksResponse:
        """Get a vendor by ID.

        Args:
            vendor_id: The vendor ID

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/vendor/{vendor_id}".format(
            vendor_id=vendor_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_vendor"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_vendor",
            )

    # -----------------------------------------------------------------------
    # Item
    # -----------------------------------------------------------------------

    async def get_item(self, item_id: str) -> QuickBooksResponse:
        """Get an item by ID.

        Args:
            item_id: The item ID

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/item/{item_id}".format(item_id=item_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_item"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_item",
            )

    # -----------------------------------------------------------------------
    # Account
    # -----------------------------------------------------------------------

    async def get_account(self, account_id: str) -> QuickBooksResponse:
        """Get an account by ID.

        Args:
            account_id: The account ID

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/account/{account_id}".format(
            account_id=account_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_account"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_account",
            )

    # -----------------------------------------------------------------------
    # Bill
    # -----------------------------------------------------------------------

    async def get_bill(self, bill_id: str) -> QuickBooksResponse:
        """Get a bill by ID.

        Args:
            bill_id: The bill ID

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/bill/{bill_id}".format(bill_id=bill_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_bill"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_bill",
            )

    # -----------------------------------------------------------------------
    # Estimate
    # -----------------------------------------------------------------------

    async def get_estimate(self, estimate_id: str) -> QuickBooksResponse:
        """Get an estimate by ID.

        Args:
            estimate_id: The estimate ID

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/estimate/{estimate_id}".format(
            estimate_id=estimate_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_estimate"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_estimate",
            )

    # -----------------------------------------------------------------------
    # Employee
    # -----------------------------------------------------------------------

    async def get_employee(self, employee_id: str) -> QuickBooksResponse:
        """Get an employee by ID.

        Args:
            employee_id: The employee ID

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/employee/{employee_id}".format(
            employee_id=employee_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_employee"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_employee",
            )

    # -----------------------------------------------------------------------
    # Company Info
    # -----------------------------------------------------------------------

    async def get_company_info(
        self, company_id: str
    ) -> QuickBooksResponse:
        """Get company information.

        Args:
            company_id: The company (realm) ID

        Returns:
            QuickBooksResponse with operation result
        """
        url = self.base_url + "/companyinfo/{company_id}".format(
            company_id=company_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuickBooksResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_company_info"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return QuickBooksResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_company_info",
            )
