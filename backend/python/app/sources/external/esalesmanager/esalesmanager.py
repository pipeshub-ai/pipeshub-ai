# ruff: noqa
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

    # -----------------------------------------------------------------------
    # Customers
    # -----------------------------------------------------------------------

    async def list_customers(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> ESalesManagerResponse:
        """List all customers

        HTTP GET /customers

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            ESalesManagerResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/customers"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_customers" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute list_customers")

    async def get_customer(
        self,
        customer_id: str
    ) -> ESalesManagerResponse:
        """Get a specific customer by ID

        HTTP GET /customers/{customer_id}

        Args:
            customer_id: The customer ID

        Returns:
            ESalesManagerResponse with operation result
        """
        url = self.base_url + "/customers/{customer_id}".format(customer_id=customer_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_customer" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute get_customer")

    # -----------------------------------------------------------------------
    # Contacts
    # -----------------------------------------------------------------------

    async def list_contacts(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> ESalesManagerResponse:
        """List all contacts

        HTTP GET /contacts

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            ESalesManagerResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/contacts"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_contacts" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute list_contacts")

    async def get_contact(
        self,
        contact_id: str
    ) -> ESalesManagerResponse:
        """Get a specific contact by ID

        HTTP GET /contacts/{contact_id}

        Args:
            contact_id: The contact ID

        Returns:
            ESalesManagerResponse with operation result
        """
        url = self.base_url + "/contacts/{contact_id}".format(contact_id=contact_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_contact" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute get_contact")

    # -----------------------------------------------------------------------
    # Activities
    # -----------------------------------------------------------------------

    async def list_activities(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> ESalesManagerResponse:
        """List all activities

        HTTP GET /activities

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            ESalesManagerResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/activities"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_activities" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute list_activities")

    async def get_activity(
        self,
        activity_id: str
    ) -> ESalesManagerResponse:
        """Get a specific activity by ID

        HTTP GET /activities/{activity_id}

        Args:
            activity_id: The activity ID

        Returns:
            ESalesManagerResponse with operation result
        """
        url = self.base_url + "/activities/{activity_id}".format(activity_id=activity_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_activity" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute get_activity")

    # -----------------------------------------------------------------------
    # Deals
    # -----------------------------------------------------------------------

    async def list_deals(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> ESalesManagerResponse:
        """List all deals

        HTTP GET /deals

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            ESalesManagerResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/deals"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_deals" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute list_deals")

    async def get_deal(
        self,
        deal_id: str
    ) -> ESalesManagerResponse:
        """Get a specific deal by ID

        HTTP GET /deals/{deal_id}

        Args:
            deal_id: The deal ID

        Returns:
            ESalesManagerResponse with operation result
        """
        url = self.base_url + "/deals/{deal_id}".format(deal_id=deal_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_deal" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute get_deal")

    # -----------------------------------------------------------------------
    # Products
    # -----------------------------------------------------------------------

    async def list_products(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> ESalesManagerResponse:
        """List all products

        HTTP GET /products

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            ESalesManagerResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/products"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_products" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute list_products")

    async def get_product(
        self,
        product_id: str
    ) -> ESalesManagerResponse:
        """Get a specific product by ID

        HTTP GET /products/{product_id}

        Args:
            product_id: The product ID

        Returns:
            ESalesManagerResponse with operation result
        """
        url = self.base_url + "/products/{product_id}".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_product" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute get_product")

    # -----------------------------------------------------------------------
    # Tasks
    # -----------------------------------------------------------------------

    async def list_tasks(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> ESalesManagerResponse:
        """List all tasks

        HTTP GET /tasks

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            ESalesManagerResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/tasks"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute list_tasks")

    async def get_task(
        self,
        task_id: str
    ) -> ESalesManagerResponse:
        """Get a specific task by ID

        HTTP GET /tasks/{task_id}

        Args:
            task_id: The task ID

        Returns:
            ESalesManagerResponse with operation result
        """
        url = self.base_url + "/tasks/{task_id}".format(task_id=task_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_task" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute get_task")

    # -----------------------------------------------------------------------
    # Reports
    # -----------------------------------------------------------------------

    async def list_reports(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> ESalesManagerResponse:
        """List all reports

        HTTP GET /reports

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            ESalesManagerResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/reports"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_reports" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute list_reports")

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def list_users(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None
    ) -> ESalesManagerResponse:
        """List all users

        HTTP GET /users

        Args:
            page: Page number for pagination
            per_page: Number of items per page

        Returns:
            ESalesManagerResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/users"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        user_id: str
    ) -> ESalesManagerResponse:
        """Get a specific user by ID

        HTTP GET /users/{user_id}

        Args:
            user_id: The user ID

        Returns:
            ESalesManagerResponse with operation result
        """
        url = self.base_url + "/users/{user_id}".format(user_id=user_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ESalesManagerResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ESalesManagerResponse(success=False, error=str(e), message="Failed to execute get_user")
