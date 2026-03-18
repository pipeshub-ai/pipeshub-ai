# ruff: noqa: A002
"""
Freshservice REST API DataSource - Auto-generated API wrapper

Generated from Freshservice REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.freshservice.freshservice import (
    FreshserviceClient,
    FreshserviceResponse,
)
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class FreshserviceDataSource:
    """Freshservice REST API DataSource

    Provides async wrapper methods for Freshservice REST API operations:
    - Ticket CRUD and management
    - Ticket conversations
    - Requesters and agents
    - Assets
    - Problems, changes, releases
    - Departments, groups
    - Service catalog items

    All methods return FreshserviceResponse objects.
    """

    def __init__(self, client: FreshserviceClient) -> None:
        """Initialize with FreshserviceClient.

        Args:
            client: FreshserviceClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'FreshserviceDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> FreshserviceClient:
        """Return the underlying FreshserviceClient."""
        return self._client

    async def list_tickets(
        self,
        page: int | None = None,
        per_page: int | None = None,
        filter_: str | None = None,
        order_by: str | None = None,
        order_type: str | None = None,
        updated_since: str | None = None,
        requester_id: int | None = None
    ) -> FreshserviceResponse:
        """List all tickets with optional filters

        Args:
            page: Page number for pagination
            per_page: Number of tickets per page (max 100)
            filter_: Predefined filter name
            order_by: Field to order by (e.g., created_at, updated_at)
            order_type: Order direction: asc or desc
            updated_since: Filter tickets updated since this timestamp (ISO format)
            requester_id: Filter by requester ID

        Returns:
            FreshserviceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if filter_ is not None:
            query_params['filter'] = filter_
        if order_by is not None:
            query_params['order_by'] = order_by
        if order_type is not None:
            query_params['order_type'] = order_type
        if updated_since is not None:
            query_params['updated_since'] = updated_since
        if requester_id is not None:
            query_params['requester_id'] = str(requester_id)

        url = self.base_url + "/tickets"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_tickets" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute list_tickets")

    async def get_ticket(
        self,
        id: int
    ) -> FreshserviceResponse:
        """Get a specific ticket by ID

        Args:
            id: Ticket ID

        Returns:
            FreshserviceResponse with operation result
        """
        url = self.base_url + "/tickets/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_ticket" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute get_ticket")

    async def create_ticket(
        self,
        subject: str,
        description: str | None = None,
        email: str | None = None,
        requester_id: int | None = None,
        phone: str | None = None,
        priority: int | None = None,
        status: int | None = None,
        source: int | None = None,
        type_: str | None = None,
        tags: list[str] | None = None,
        cc_emails: list[str] | None = None,
        custom_fields: dict[str, Any] | None = None,
        department_id: int | None = None,
        group_id: int | None = None,
        category: str | None = None,
        sub_category: str | None = None,
        item_category: str | None = None,
        responder_id: int | None = None,
        due_by: str | None = None,
        fr_due_by: str | None = None,
        urgency: int | None = None,
        impact: int | None = None
    ) -> FreshserviceResponse:
        """Create a new ticket

        Args:
            subject: Subject of the ticket
            description: HTML content of the ticket
            email: Email of the requester
            requester_id: User ID of the requester
            phone: Phone number of the requester
            priority: Priority: 1=Low, 2=Medium, 3=High, 4=Urgent
            status: Status: 2=Open, 3=Pending, 4=Resolved, 5=Closed
            source: Source of the ticket
            type_: Type of the ticket
            tags: Tags for the ticket
            cc_emails: CC email addresses
            custom_fields: Custom field values
            department_id: Department ID
            group_id: Group ID
            category: Category of the ticket
            sub_category: Sub-category of the ticket
            item_category: Item category
            responder_id: Agent ID to assign
            due_by: Due date (ISO format)
            fr_due_by: First response due date (ISO format)
            urgency: Urgency of the ticket
            impact: Impact of the ticket

        Returns:
            FreshserviceResponse with operation result
        """
        url = self.base_url + "/tickets"

        request_body: dict[str, Any] = {}
        request_body['subject'] = subject
        if description is not None:
            request_body['description'] = description
        if email is not None:
            request_body['email'] = email
        if requester_id is not None:
            request_body['requester_id'] = requester_id
        if phone is not None:
            request_body['phone'] = phone
        if priority is not None:
            request_body['priority'] = priority
        if status is not None:
            request_body['status'] = status
        if source is not None:
            request_body['source'] = source
        if type_ is not None:
            request_body['type'] = type_
        if tags is not None:
            request_body['tags'] = tags
        if cc_emails is not None:
            request_body['cc_emails'] = cc_emails
        if custom_fields is not None:
            request_body['custom_fields'] = custom_fields
        if department_id is not None:
            request_body['department_id'] = department_id
        if group_id is not None:
            request_body['group_id'] = group_id
        if category is not None:
            request_body['category'] = category
        if sub_category is not None:
            request_body['sub_category'] = sub_category
        if item_category is not None:
            request_body['item_category'] = item_category
        if responder_id is not None:
            request_body['responder_id'] = responder_id
        if due_by is not None:
            request_body['due_by'] = due_by
        if fr_due_by is not None:
            request_body['fr_due_by'] = fr_due_by
        if urgency is not None:
            request_body['urgency'] = urgency
        if impact is not None:
            request_body['impact'] = impact

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=request_body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_ticket" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute create_ticket")

    async def update_ticket(
        self,
        id: int,
        subject: str | None = None,
        description: str | None = None,
        priority: int | None = None,
        status: int | None = None,
        type_: str | None = None,
        tags: list[str] | None = None,
        custom_fields: dict[str, Any] | None = None,
        department_id: int | None = None,
        group_id: int | None = None,
        category: str | None = None,
        sub_category: str | None = None,
        item_category: str | None = None,
        responder_id: int | None = None,
        urgency: int | None = None,
        impact: int | None = None
    ) -> FreshserviceResponse:
        """Update an existing ticket

        Args:
            id: Ticket ID
            subject: Subject of the ticket
            description: HTML content of the ticket
            priority: Priority: 1=Low, 2=Medium, 3=High, 4=Urgent
            status: Status: 2=Open, 3=Pending, 4=Resolved, 5=Closed
            type_: Type of the ticket
            tags: Tags for the ticket
            custom_fields: Custom field values
            department_id: Department ID
            group_id: Group ID
            category: Category
            sub_category: Sub-category
            item_category: Item category
            responder_id: Agent ID to assign
            urgency: Urgency
            impact: Impact

        Returns:
            FreshserviceResponse with operation result
        """
        url = self.base_url + "/tickets/{id}".format(id=id)

        request_body: dict[str, Any] = {}
        if subject is not None:
            request_body['subject'] = subject
        if description is not None:
            request_body['description'] = description
        if priority is not None:
            request_body['priority'] = priority
        if status is not None:
            request_body['status'] = status
        if type_ is not None:
            request_body['type'] = type_
        if tags is not None:
            request_body['tags'] = tags
        if custom_fields is not None:
            request_body['custom_fields'] = custom_fields
        if department_id is not None:
            request_body['department_id'] = department_id
        if group_id is not None:
            request_body['group_id'] = group_id
        if category is not None:
            request_body['category'] = category
        if sub_category is not None:
            request_body['sub_category'] = sub_category
        if item_category is not None:
            request_body['item_category'] = item_category
        if responder_id is not None:
            request_body['responder_id'] = responder_id
        if urgency is not None:
            request_body['urgency'] = urgency
        if impact is not None:
            request_body['impact'] = impact

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=request_body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_ticket" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute update_ticket")

    async def delete_ticket(
        self,
        id: int
    ) -> FreshserviceResponse:
        """Delete a ticket

        Args:
            id: Ticket ID

        Returns:
            FreshserviceResponse with operation result
        """
        url = self.base_url + "/tickets/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_ticket" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute delete_ticket")

    async def list_ticket_conversations(
        self,
        id: int,
        page: int | None = None,
        per_page: int | None = None
    ) -> FreshserviceResponse:
        """List all conversations of a ticket

        Args:
            id: Ticket ID
            page: Page number
            per_page: Items per page

        Returns:
            FreshserviceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/tickets/{id}/conversations".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_ticket_conversations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute list_ticket_conversations")

    async def list_requesters(
        self,
        page: int | None = None,
        per_page: int | None = None,
        email: str | None = None,
        query_: str | None = None
    ) -> FreshserviceResponse:
        """List all requesters

        Args:
            page: Page number
            per_page: Items per page
            email: Filter by email
            query_: Search query string

        Returns:
            FreshserviceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if email is not None:
            query_params['email'] = email
        if query_ is not None:
            query_params['query'] = query_

        url = self.base_url + "/requesters"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_requesters" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute list_requesters")

    async def get_requester(
        self,
        id: int
    ) -> FreshserviceResponse:
        """Get a specific requester by ID

        Args:
            id: Requester ID

        Returns:
            FreshserviceResponse with operation result
        """
        url = self.base_url + "/requesters/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_requester" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute get_requester")

    async def list_agents(
        self,
        page: int | None = None,
        per_page: int | None = None,
        email: str | None = None,
        state: str | None = None
    ) -> FreshserviceResponse:
        """List all agents

        Args:
            page: Page number
            per_page: Items per page
            email: Filter by email
            state: Filter by agent state (fulltime, occasional)

        Returns:
            FreshserviceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if email is not None:
            query_params['email'] = email
        if state is not None:
            query_params['state'] = state

        url = self.base_url + "/agents"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_agents" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute list_agents")

    async def get_agent(
        self,
        id: int
    ) -> FreshserviceResponse:
        """Get a specific agent by ID

        Args:
            id: Agent ID

        Returns:
            FreshserviceResponse with operation result
        """
        url = self.base_url + "/agents/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_agent" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute get_agent")

    async def list_assets(
        self,
        page: int | None = None,
        per_page: int | None = None,
        filter_: str | None = None
    ) -> FreshserviceResponse:
        """List all assets

        Args:
            page: Page number
            per_page: Items per page
            filter_: Filter name

        Returns:
            FreshserviceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if filter_ is not None:
            query_params['filter'] = filter_

        url = self.base_url + "/assets"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_assets" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute list_assets")

    async def get_asset(
        self,
        display_id: int
    ) -> FreshserviceResponse:
        """Get a specific asset by display ID

        Args:
            display_id: Asset display ID

        Returns:
            FreshserviceResponse with operation result
        """
        url = self.base_url + "/assets/{display_id}".format(display_id=display_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_asset" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute get_asset")

    async def list_problems(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> FreshserviceResponse:
        """List all problems

        Args:
            page: Page number
            per_page: Items per page

        Returns:
            FreshserviceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/problems"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_problems" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute list_problems")

    async def list_changes(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> FreshserviceResponse:
        """List all changes

        Args:
            page: Page number
            per_page: Items per page

        Returns:
            FreshserviceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/changes"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_changes" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute list_changes")

    async def list_releases(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> FreshserviceResponse:
        """List all releases

        Args:
            page: Page number
            per_page: Items per page

        Returns:
            FreshserviceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/releases"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_releases" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute list_releases")

    async def list_departments(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> FreshserviceResponse:
        """List all departments

        Args:
            page: Page number
            per_page: Items per page

        Returns:
            FreshserviceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/departments"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_departments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute list_departments")

    async def list_groups(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> FreshserviceResponse:
        """List all groups

        Args:
            page: Page number
            per_page: Items per page

        Returns:
            FreshserviceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/groups"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute list_groups")

    async def list_service_catalog_items(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> FreshserviceResponse:
        """List all service catalog items

        Args:
            page: Page number
            per_page: Items per page

        Returns:
            FreshserviceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/service_catalog/items"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FreshserviceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_service_catalog_items" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FreshserviceResponse(success=False, error=str(e), message="Failed to execute list_service_catalog_items")
