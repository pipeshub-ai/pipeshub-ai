"""
FreshDesk DataSource - Auto-generated API wrapper

Generated from FreshDesk API documentation.
Uses HTTP client for direct REST API interactions.
"""

import logging
from typing import Any, Dict, List, Optional

from app.sources.client.freshdesk.freshdesk import (
    FreshDeskClient,
    FreshDeskResponse,
)
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse

logger = logging.getLogger(__name__)

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class FreshdeskDataSource:
    """FreshDesk API DataSource

    Provides async wrapper methods for FreshDesk API operations.
    All methods return standardized FreshDeskResponse objects.

    Generated methods: 50
    """

    def __init__(self, freshdeskClient: FreshDeskClient) -> None:
        """Initialize FreshDesk DataSource

        Args:
            freshdeskClient: FreshDeskClient instance
        """
        self.http_client = freshdeskClient.get_client()
        self._freshdesk_client = freshdeskClient

    def get_client(self) -> FreshDeskClient:
        """Get the underlying FreshDeskClient"""
        return self._freshdesk_client

    async def create_ticket(
        self,
        subject: str,
        description: Optional[str] = None,
        email: Optional[str] = None,
        requester_id: Optional[int] = None,
        phone: Optional[str] = None,
        priority: Optional[int] = 1,
        status: Optional[int] = 2,
        source: Optional[int] = 2,
        tags: Optional[List[str]] = None,
        cc_emails: Optional[List[str]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[str]] = None
    ) -> FreshDeskResponse:
        """Create a new ticket in FreshDesk

        API Endpoint: POST /api/v2/tickets

        Args:
            subject (str, required): Subject of the ticket
            description (str, optional): HTML content of the ticket
            email (str, optional): Email address of the requester
            requester_id (int, optional): User ID of the requester
            phone (str, optional): Phone number of the requester
            priority (int, optional): Priority of the ticket (1-4)
            status (int, optional): Status of the ticket (2-5)
            source (int, optional): Source of the ticket
            tags (List[str], optional): Tags associated with the ticket
            cc_emails (List[str], optional): CC email addresses
            custom_fields (Dict[str, Any], optional): Custom field values
            attachments (List[str], optional): File paths for attachments

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            ticket = await ds.create_ticket(subject="Issue", email="user@example.com")
        """
        url = self._freshdesk_client.get_base_url()
        url += "/tickets"
        request_body: Dict[str, Any] = {}
        if subject is not None:
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
        if tags is not None:
            request_body['tags'] = tags
        if cc_emails is not None:
            request_body['cc_emails'] = cc_emails
        if custom_fields is not None:
            request_body['custom_fields'] = custom_fields
        if attachments is not None:
            request_body['attachments'] = attachments

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"create_ticket: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed create_ticket" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in create_ticket: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute create_ticket"
            )

    async def create_outbound_email(
        self,
        subject: str,
        email: str,
        description: Optional[str] = None,
        priority: Optional[int] = 1,
        status: Optional[int] = 5,
        custom_fields: Optional[Dict[str, Any]] = None
    ) -> FreshDeskResponse:
        """Create an outbound email ticket

        API Endpoint: POST /api/v2/tickets/outbound_email

        Args:
            subject (str, required): Subject of the ticket
            email (str, required): Email address of the recipient
            description (str, optional): HTML content of the ticket
            priority (int, optional): Priority of the ticket (1-4)
            status (int, optional): Status of the ticket (2-5)
            custom_fields (Dict[str, Any], optional): Custom field values

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            ticket = await ds.create_outbound_email(subject="Info", email="user@example.com")
        """
        url = self._freshdesk_client.get_base_url()
        url += "/tickets/outbound_email"
        request_body: Dict[str, Any] = {}
        if subject is not None:
            request_body['subject'] = subject
        if email is not None:
            request_body['email'] = email
        if description is not None:
            request_body['description'] = description
        if priority is not None:
            request_body['priority'] = priority
        if status is not None:
            request_body['status'] = status
        if custom_fields is not None:
            request_body['custom_fields'] = custom_fields

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"create_outbound_email: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed create_outbound_email" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in create_outbound_email: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute create_outbound_email"
            )

    async def get_ticket(
        self,
        id: int,
        include: Optional[str] = None
    ) -> FreshDeskResponse:
        """Retrieve a specific ticket by ID

        API Endpoint: GET /api/v2/tickets/[id]

        Args:
            id (int, required): ID of the ticket to retrieve
            include (str, optional): Embed additional details (conversations, requester, company, stats)

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            ticket = await ds.get_ticket(id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/tickets/{id}"
        params = {}
        if include is not None:
            params['include'] = include
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"get_ticket: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed get_ticket" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in get_ticket: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_ticket"
            )

    async def list_tickets(
        self,
        filter_name: Optional[str] = None,
        updated_since: Optional[str] = None,
        page: Optional[int] = 1,
        per_page: Optional[int] = 30,
        include: Optional[str] = None
    ) -> FreshDeskResponse:
        """List all tickets with optional filters

        API Endpoint: GET /api/v2/tickets

        Args:
            filter_name (str, optional): Predefined filter (new_and_my_open, watching, spam, deleted)
            updated_since (str, optional): Filter tickets updated after this timestamp (ISO format)
            page (int, optional): Page number for pagination
            per_page (int, optional): Number of tickets per page (max 100)
            include (str, optional): Embed additional details

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            tickets = await ds.list_tickets(filter_name="new_and_my_open", per_page=10)
        """
        url = self._freshdesk_client.get_base_url()
        url += "/tickets"
        params = {}
        if filter_name is not None:
            params['filter_name'] = filter_name
        if updated_since is not None:
            params['updated_since'] = updated_since
        if page is not None:
            params['page'] = page
        if per_page is not None:
            params['per_page'] = per_page
        if include is not None:
            params['include'] = include
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_tickets: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_tickets" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_tickets: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_tickets"
            )

    async def filter_tickets(
        self,
        query: str,
        page: Optional[int] = 1
    ) -> FreshDeskResponse:
        """Filter tickets using custom query

        API Endpoint: GET /api/v2/search/tickets

        Args:
            query (str, required): Filter query string (e.g., 'priority:3')
            page (int, optional): Page number for pagination

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            result = await ds.filter_tickets(query="priority:3 AND status:2")
        """
        url = self._freshdesk_client.get_base_url()
        url += "/search/tickets"
        params = {}
        if query is not None:
            params['query'] = query
        if page is not None:
            params['page'] = page
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"filter_tickets: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed filter_tickets" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in filter_tickets: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute filter_tickets"
            )

    async def update_ticket(
        self,
        id: int,
        subject: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[int] = None,
        status: Optional[int] = None,
        tags: Optional[List[str]] = None,
        custom_fields: Optional[Dict[str, Any]] = None
    ) -> FreshDeskResponse:
        """Update an existing ticket

        API Endpoint: PUT /api/v2/tickets/[id]

        Args:
            id (int, required): ID of the ticket to update
            subject (str, optional): New subject for the ticket
            description (str, optional): New description
            priority (int, optional): New priority (1-4)
            status (int, optional): New status (2-5)
            tags (List[str], optional): Tags to associate
            custom_fields (Dict[str, Any], optional): Custom field values

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            ticket = await ds.update_ticket(ticket_id=123, priority=4, status=3)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/tickets/{id}"
        request_body: Dict[str, Any] = {}
        if subject is not None:
            request_body['subject'] = subject
        if description is not None:
            request_body['description'] = description
        if priority is not None:
            request_body['priority'] = priority
        if status is not None:
            request_body['status'] = status
        if tags is not None:
            request_body['tags'] = tags
        if custom_fields is not None:
            request_body['custom_fields'] = custom_fields

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"update_ticket: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed update_ticket" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in update_ticket: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute update_ticket"
            )

    async def delete_ticket(
        self,
        id: int
    ) -> FreshDeskResponse:
        """Delete a ticket (moves to trash)

        API Endpoint: DELETE /api/v2/tickets/[id]

        Args:
            id (int, required): ID of the ticket to delete

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            await ds.delete_ticket(ticket_id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/tickets/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"delete_ticket: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed delete_ticket" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in delete_ticket: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute delete_ticket"
            )

    async def list_ticket_fields(
        self,
        type: Optional[str] = None
    ) -> FreshDeskResponse:
        """List all ticket fields including custom fields

        API Endpoint: GET /api/v2/ticket_fields

        Args:
            type (str, optional): Filter by field type

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            fields = await ds.list_ticket_fields()
        """
        url = self._freshdesk_client.get_base_url()
        url += "/ticket_fields"
        params = {}
        if type is not None:
            params['type'] = type
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_ticket_fields: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_ticket_fields" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_ticket_fields: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_ticket_fields"
            )

    async def list_ticket_conversations(
        self,
        id: int,
        page: Optional[int] = 1,
        per_page: Optional[int] = 30
    ) -> FreshDeskResponse:
        """List all conversations (notes/replies) for a ticket

        API Endpoint: GET /api/v2/tickets/[id]/conversations

        Args:
            id (int, required): ID of the ticket
            page (int, optional): Page number for pagination
            per_page (int, optional): Number of conversations per page

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            conversations = await ds.list_ticket_conversations(ticket_id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/tickets/{id}/conversations"
        params = {}
        if page is not None:
            params['page'] = page
        if per_page is not None:
            params['per_page'] = per_page
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_ticket_conversations: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_ticket_conversations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_ticket_conversations: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_ticket_conversations"
            )

    async def create_note(
        self,
        id: int,
        body: str,
        private: Optional[bool] = True,
        notify_emails: Optional[List[str]] = None
    ) -> FreshDeskResponse:
        """Add a note to a ticket

        API Endpoint: POST /api/v2/tickets/[id]/notes

        Args:
            id (int, required): ID of the ticket
            body (str, required): Content of the note
            private (bool, optional): Whether the note is private
            notify_emails (List[str], optional): Email addresses to notify

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            note = await ds.create_note(ticket_id=123, body="Internal note", private=True)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/tickets/{id}/notes"
        request_body: Dict[str, Any] = {}
        if body is not None:
            request_body['body'] = body
        if private is not None:
            request_body['private'] = private
        if notify_emails is not None:
            request_body['notify_emails'] = notify_emails

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"create_note: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed create_note" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in create_note: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute create_note"
            )

    async def create_reply(
        self,
        id: int,
        body: str,
        cc_emails: Optional[List[str]] = None,
        bcc_emails: Optional[List[str]] = None
    ) -> FreshDeskResponse:
        """Reply to a ticket

        API Endpoint: POST /api/v2/tickets/[id]/reply

        Args:
            id (int, required): ID of the ticket
            body (str, required): Content of the reply
            cc_emails (List[str], optional): CC email addresses
            bcc_emails (List[str], optional): BCC email addresses

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            reply = await ds.create_reply(ticket_id=123, body="Thank you for reporting")
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/tickets/{id}/reply"
        request_body: Dict[str, Any] = {}
        if body is not None:
            request_body['body'] = body
        if cc_emails is not None:
            request_body['cc_emails'] = cc_emails
        if bcc_emails is not None:
            request_body['bcc_emails'] = bcc_emails

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"create_reply: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed create_reply" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in create_reply: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute create_reply"
            )

    async def list_deleted_tickets(
        self,
        page: Optional[int] = 1,
        per_page: Optional[int] = 30
    ) -> FreshDeskResponse:
        """List all deleted tickets

        API Endpoint: GET /api/v2/tickets

        Args:
            page (int, optional): Page number for pagination
            per_page (int, optional): Number of tickets per page

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            deleted = await ds.list_deleted_tickets()
        """
        url = self._freshdesk_client.get_base_url()
        url += "/tickets"
        params = {}
        if page is not None:
            params['page'] = page
        if per_page is not None:
            params['per_page'] = per_page
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_deleted_tickets: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_deleted_tickets" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_deleted_tickets: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_deleted_tickets"
            )

    async def restore_ticket(
        self,
        id: int
    ) -> FreshDeskResponse:
        """Restore a deleted ticket

        API Endpoint: PUT /api/v2/tickets/[id]/restore

        Args:
            id (int, required): ID of the ticket to restore

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            await ds.restore_ticket(ticket_id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/tickets/{id}/restore"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"restore_ticket: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed restore_ticket" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in restore_ticket: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute restore_ticket"
            )

    async def create_problem(
        self,
        subject: str,
        description: Optional[str] = None,
        requester_id: Optional[int] = None,
        priority: Optional[int] = 1,
        status: Optional[int] = 1,
        impact: Optional[int] = 1,
        group_id: Optional[int] = None,
        agent_id: Optional[int] = None,
        department_id: Optional[int] = None,
        category: Optional[str] = None,
        sub_category: Optional[str] = None,
        item_category: Optional[str] = None,
        due_by: Optional[str] = None,
        known_error: Optional[bool] = False,
        custom_fields: Optional[Dict[str, Any]] = None,
        assets: Optional[List[Dict[str, int]]] = None
    ) -> FreshDeskResponse:
        """Create a new problem in FreshDesk

        API Endpoint: POST /api/v2/problems

        Args:
            subject (str, required): Subject of the problem
            description (str, optional): HTML content of the problem
            requester_id (int, optional): User ID of the requester
            priority (int, optional): Priority of the problem (1-4)
            status (int, optional): Status of the problem (1-3)
            impact (int, optional): Impact of the problem (1-3)
            group_id (int, optional): ID of the group to assign
            agent_id (int, optional): ID of the agent to assign
            department_id (int, optional): ID of the department
            category (str, optional): Category of the problem
            sub_category (str, optional): Sub-category of the problem
            item_category (str, optional): Item category
            due_by (str, optional): Due date (ISO format)
            known_error (bool, optional): Whether this is a known error
            custom_fields (Dict[str, Any], optional): Custom field values
            assets (List[Dict[str, int]], optional): Associated assets

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            problem = await ds.create_problem(subject="Root cause", requester_id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += "/problems"
        request_body: Dict[str, Any] = {}
        if subject is not None:
            request_body['subject'] = subject
        if description is not None:
            request_body['description'] = description
        if requester_id is not None:
            request_body['requester_id'] = requester_id
        if priority is not None:
            request_body['priority'] = priority
        if status is not None:
            request_body['status'] = status
        if impact is not None:
            request_body['impact'] = impact
        if group_id is not None:
            request_body['group_id'] = group_id
        if agent_id is not None:
            request_body['agent_id'] = agent_id
        if department_id is not None:
            request_body['department_id'] = department_id
        if category is not None:
            request_body['category'] = category
        if sub_category is not None:
            request_body['sub_category'] = sub_category
        if item_category is not None:
            request_body['item_category'] = item_category
        if due_by is not None:
            request_body['due_by'] = due_by
        if known_error is not None:
            request_body['known_error'] = known_error
        if custom_fields is not None:
            request_body['custom_fields'] = custom_fields
        if assets is not None:
            request_body['assets'] = assets

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"create_problem: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed create_problem" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in create_problem: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute create_problem"
            )

    async def get_problem(
        self,
        id: int
    ) -> FreshDeskResponse:
        """Retrieve a specific problem by ID

        API Endpoint: GET /api/v2/problems/[id]

        Args:
            id (int, required): ID of the problem to retrieve

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            problem = await ds.get_problem(id=456)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/problems/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"get_problem: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed get_problem" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in get_problem: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_problem"
            )

    async def list_problems(
        self,
        predefined_filter: Optional[str] = None,
        requester_id: Optional[int] = None,
        page: Optional[int] = 1,
        per_page: Optional[int] = 30
    ) -> FreshDeskResponse:
        """List all problems with optional filters

        API Endpoint: GET /api/v2/problems

        Args:
            predefined_filter (str, optional): Predefined filter name
            requester_id (int, optional): Filter by requester ID
            page (int, optional): Page number for pagination
            per_page (int, optional): Number of problems per page (max 100)

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            problems = await ds.list_problems(per_page=10)
        """
        url = self._freshdesk_client.get_base_url()
        url += "/problems"
        params = {}
        if predefined_filter is not None:
            params['predefined_filter'] = predefined_filter
        if requester_id is not None:
            params['requester_id'] = requester_id
        if page is not None:
            params['page'] = page
        if per_page is not None:
            params['per_page'] = per_page
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_problems: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_problems" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_problems: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_problems"
            )

    async def update_problem(
        self,
        id: int,
        subject: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[int] = None,
        status: Optional[int] = None,
        impact: Optional[int] = None,
        known_error: Optional[bool] = None,
        group_id: Optional[int] = None,
        agent_id: Optional[int] = None,
        custom_fields: Optional[Dict[str, Any]] = None
    ) -> FreshDeskResponse:
        """Update an existing problem

        API Endpoint: PUT /api/v2/problems/[id]

        Args:
            id (int, required): ID of the problem to update
            subject (str, optional): New subject
            description (str, optional): New description
            priority (int, optional): New priority (1-4)
            status (int, optional): New status (1-3)
            impact (int, optional): New impact (1-3)
            known_error (bool, optional): Mark as known error
            group_id (int, optional): Reassign to group
            agent_id (int, optional): Reassign to agent
            custom_fields (Dict[str, Any], optional): Custom field values

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            problem = await ds.update_problem(id=456, status=2, priority=3)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/problems/{id}"
        request_body: Dict[str, Any] = {}
        if subject is not None:
            request_body['subject'] = subject
        if description is not None:
            request_body['description'] = description
        if priority is not None:
            request_body['priority'] = priority
        if status is not None:
            request_body['status'] = status
        if impact is not None:
            request_body['impact'] = impact
        if known_error is not None:
            request_body['known_error'] = known_error
        if group_id is not None:
            request_body['group_id'] = group_id
        if agent_id is not None:
            request_body['agent_id'] = agent_id
        if custom_fields is not None:
            request_body['custom_fields'] = custom_fields

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"update_problem: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed update_problem" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in update_problem: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute update_problem"
            )

    async def delete_problem(
        self,
        id: int
    ) -> FreshDeskResponse:
        """Delete a problem (moves to trash)

        API Endpoint: DELETE /api/v2/problems/[id]

        Args:
            id (int, required): ID of the problem to delete

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            await ds.delete_problem(id=456)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/problems/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"delete_problem: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed delete_problem" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in delete_problem: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute delete_problem"
            )

    async def restore_problem(
        self,
        id: int
    ) -> FreshDeskResponse:
        """Restore a deleted problem

        API Endpoint: PUT /api/v2/problems/[id]/restore

        Args:
            id (int, required): ID of the problem to restore

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            await ds.restore_problem(id=456)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/problems/{id}/restore"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"restore_problem: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed restore_problem" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in restore_problem: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute restore_problem"
            )

    async def list_deleted_problems(
        self,
        page: Optional[int] = 1,
        per_page: Optional[int] = 30
    ) -> FreshDeskResponse:
        """List all deleted problems

        API Endpoint: GET /api/v2/problems/deleted

        Args:
            page (int, optional): Page number for pagination
            per_page (int, optional): Number per page

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            deleted = await ds.list_deleted_problems()
        """
        url = self._freshdesk_client.get_base_url()
        url += "/problems/deleted"
        params = {}
        if page is not None:
            params['page'] = page
        if per_page is not None:
            params['per_page'] = per_page
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_deleted_problems: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_deleted_problems" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_deleted_problems: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_deleted_problems"
            )

    async def create_problem_note(
        self,
        id: int,
        body: str,
        notify_emails: Optional[List[str]] = None
    ) -> FreshDeskResponse:
        """Add a note to a problem

        API Endpoint: POST /api/v2/problems/[id]/notes

        Args:
            id (int, required): ID of the problem
            body (str, required): Content of the note
            notify_emails (List[str], optional): Emails to notify

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            note = await ds.create_problem_note(id=456, body="Root cause identified")
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/problems/{id}/notes"
        request_body: Dict[str, Any] = {}
        if body is not None:
            request_body['body'] = body
        if notify_emails is not None:
            request_body['notify_emails'] = notify_emails

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"create_problem_note: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed create_problem_note" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in create_problem_note: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute create_problem_note"
            )

    async def list_problem_tasks(
        self,
        id: int,
        page: Optional[int] = 1,
        per_page: Optional[int] = 30
    ) -> FreshDeskResponse:
        """List all tasks associated with a problem

        API Endpoint: GET /api/v2/problems/[id]/tasks

        Args:
            id (int, required): ID of the problem
            page (int, optional): Page number
            per_page (int, optional): Number per page

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            tasks = await ds.list_problem_tasks(id=456)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/problems/{id}/tasks"
        params = {}
        if page is not None:
            params['page'] = page
        if per_page is not None:
            params['per_page'] = per_page
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_problem_tasks: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_problem_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_problem_tasks: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_problem_tasks"
            )

    async def create_problem_task(
        self,
        id: int,
        title: str,
        description: Optional[str] = None,
        status: Optional[int] = 1,
        due_date: Optional[str] = None,
        notify_before: Optional[int] = None,
        agent_id: Optional[int] = None,
        group_id: Optional[int] = None
    ) -> FreshDeskResponse:
        """Create a task for a problem

        API Endpoint: POST /api/v2/problems/[id]/tasks

        Args:
            id (int, required): ID of the problem
            title (str, required): Title of the task
            description (str, optional): Description of the task
            status (int, optional): Status of the task (1-3)
            due_date (str, optional): Due date (ISO format)
            notify_before (int, optional): Notify before due (in hours)
            agent_id (int, optional): Agent assigned to task
            group_id (int, optional): Group assigned to task

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            task = await ds.create_problem_task(id=456, title="Investigate logs")
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/problems/{id}/tasks"
        request_body: Dict[str, Any] = {}
        if title is not None:
            request_body['title'] = title
        if description is not None:
            request_body['description'] = description
        if status is not None:
            request_body['status'] = status
        if due_date is not None:
            request_body['due_date'] = due_date
        if notify_before is not None:
            request_body['notify_before'] = notify_before
        if agent_id is not None:
            request_body['agent_id'] = agent_id
        if group_id is not None:
            request_body['group_id'] = group_id

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"create_problem_task: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed create_problem_task" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in create_problem_task: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute create_problem_task"
            )

    async def update_problem_task(
        self,
        problem_id: int,
        task_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[int] = None,
        agent_id: Optional[int] = None
    ) -> FreshDeskResponse:
        """Update a problem task

        API Endpoint: PUT /api/v2/problems/[problem_id]/tasks/[task_id]

        Args:
            problem_id (int, required): ID of the problem
            task_id (int, required): ID of the task
            title (str, optional): New title
            description (str, optional): New description
            status (int, optional): New status (1-3)
            agent_id (int, optional): Reassign to agent

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            task = await ds.update_problem_task(problem_id=456, task_id=1, status=2)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/problems/{problem_id}/tasks/{task_id}"
        request_body: Dict[str, Any] = {}
        if title is not None:
            request_body['title'] = title
        if description is not None:
            request_body['description'] = description
        if status is not None:
            request_body['status'] = status
        if agent_id is not None:
            request_body['agent_id'] = agent_id

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"update_problem_task: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed update_problem_task" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in update_problem_task: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute update_problem_task"
            )

    async def delete_problem_task(
        self,
        problem_id: int,
        task_id: int
    ) -> FreshDeskResponse:
        """Delete a problem task

        API Endpoint: DELETE /api/v2/problems/[problem_id]/tasks/[task_id]

        Args:
            problem_id (int, required): ID of the problem
            task_id (int, required): ID of the task

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            await ds.delete_problem_task(problem_id=456, task_id=1)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/problems/{problem_id}/tasks/{task_id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"delete_problem_task: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed delete_problem_task" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in delete_problem_task: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute delete_problem_task"
            )

    async def list_problem_time_entries(
        self,
        id: int
    ) -> FreshDeskResponse:
        """List all time entries for a problem

        API Endpoint: GET /api/v2/problems/[id]/time_entries

        Args:
            id (int, required): ID of the problem

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            entries = await ds.list_problem_time_entries(id=456)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/problems/{id}/time_entries"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_problem_time_entries: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_problem_time_entries" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_problem_time_entries: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_problem_time_entries"
            )

    async def create_agent(
        self,
        first_name: str,
        email: str,
        last_name: Optional[str] = None,
        occasional: Optional[bool] = False,
        job_title: Optional[str] = None,
        work_phone_number: Optional[str] = None,
        mobile_phone_number: Optional[str] = None,
        department_ids: Optional[List[int]] = None,
        can_see_all_tickets_from_associated_departments: Optional[bool] = False,
        reporting_manager_id: Optional[int] = None,
        address: Optional[str] = None,
        time_zone: Optional[str] = None,
        time_format: Optional[str] = None,
        language: Optional[str] = None,
        location_id: Optional[int] = None,
        background_information: Optional[str] = None,
        scoreboard_level_id: Optional[int] = None,
        roles: Optional[List[Dict[str, Any]]] = None,
        signature: Optional[str] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        workspace_ids: Optional[List[int]] = None
    ) -> FreshDeskResponse:
        """Create a new agent in FreshDesk

        API Endpoint: POST /api/v2/agents

        Args:
            first_name (str, required): First name of the agent
            email (str, required): Email address of the agent
            last_name (str, optional): Last name of the agent
            occasional (bool, optional): True if occasional agent, false if full-time
            job_title (str, optional): Job title of the agent
            work_phone_number (str, optional): Work phone number
            mobile_phone_number (str, optional): Mobile phone number
            department_ids (List[int], optional): IDs of departments
            can_see_all_tickets_from_associated_departments (bool, optional): Can view all department tickets
            reporting_manager_id (int, optional): User ID of reporting manager
            address (str, optional): Address of the agent
            time_zone (str, optional): Time zone
            time_format (str, optional): Time format (12h or 24h)
            language (str, optional): Language code
            location_id (int, optional): Location ID
            background_information (str, optional): Background information
            scoreboard_level_id (int, optional): Scoreboard level ID
            roles (List[Dict[str, Any]], optional): Array of role objects
            signature (str, optional): Signature in HTML format
            custom_fields (Dict[str, Any], optional): Custom field values
            workspace_ids (List[int], optional): Workspace IDs

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            agent = await ds.create_agent(first_name="John", email="john@example.com")
        """
        url = self._freshdesk_client.get_base_url()
        url += "/agents"
        request_body: Dict[str, Any] = {}
        if first_name is not None:
            request_body['first_name'] = first_name
        if email is not None:
            request_body['email'] = email
        if last_name is not None:
            request_body['last_name'] = last_name
        if occasional is not None:
            request_body['occasional'] = occasional
        if job_title is not None:
            request_body['job_title'] = job_title
        if work_phone_number is not None:
            request_body['work_phone_number'] = work_phone_number
        if mobile_phone_number is not None:
            request_body['mobile_phone_number'] = mobile_phone_number
        if department_ids is not None:
            request_body['department_ids'] = department_ids
        if can_see_all_tickets_from_associated_departments is not None:
            request_body['can_see_all_tickets_from_associated_departments'] = can_see_all_tickets_from_associated_departments
        if reporting_manager_id is not None:
            request_body['reporting_manager_id'] = reporting_manager_id
        if address is not None:
            request_body['address'] = address
        if time_zone is not None:
            request_body['time_zone'] = time_zone
        if time_format is not None:
            request_body['time_format'] = time_format
        if language is not None:
            request_body['language'] = language
        if location_id is not None:
            request_body['location_id'] = location_id
        if background_information is not None:
            request_body['background_information'] = background_information
        if scoreboard_level_id is not None:
            request_body['scoreboard_level_id'] = scoreboard_level_id
        if roles is not None:
            request_body['roles'] = roles
        if signature is not None:
            request_body['signature'] = signature
        if custom_fields is not None:
            request_body['custom_fields'] = custom_fields
        if workspace_ids is not None:
            request_body['workspace_ids'] = workspace_ids

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"create_agent: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed create_agent" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in create_agent: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute create_agent"
            )

    async def view_agent(
        self,
        id: int
    ) -> FreshDeskResponse:
        """View information about a specific agent

        API Endpoint: GET /api/v2/agents/[id]

        Args:
            id (int, required): ID of the agent

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            agent = await ds.view_agent(id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/agents/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"view_agent: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed view_agent" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in view_agent: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute view_agent"
            )

    async def list_agents(
        self,
        email: Optional[str] = None,
        mobile_phone_number: Optional[str] = None,
        work_phone_number: Optional[str] = None,
        active: Optional[bool] = None,
        state: Optional[str] = None,
        page: Optional[int] = 1,
        per_page: Optional[int] = 30
    ) -> FreshDeskResponse:
        """List all agents in the account

        API Endpoint: GET /api/v2/agents

        Args:
            email (str, optional): Filter by email
            mobile_phone_number (str, optional): Filter by mobile phone
            work_phone_number (str, optional): Filter by work phone
            active (bool, optional): Filter by active status
            state (str, optional): Filter by state (fulltime/occasional)
            page (int, optional): Page number
            per_page (int, optional): Number of entries per page

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            agents = await ds.list_agents(active=True)
        """
        url = self._freshdesk_client.get_base_url()
        url += "/agents"
        params = {}
        if email is not None:
            params['email'] = email
        if mobile_phone_number is not None:
            params['mobile_phone_number'] = mobile_phone_number
        if work_phone_number is not None:
            params['work_phone_number'] = work_phone_number
        if active is not None:
            params['active'] = active
        if state is not None:
            params['state'] = state
        if page is not None:
            params['page'] = page
        if per_page is not None:
            params['per_page'] = per_page
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_agents: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_agents" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_agents: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_agents"
            )

    async def filter_agents(
        self,
        query: str,
        page: Optional[int] = 1,
        per_page: Optional[int] = 30
    ) -> FreshDeskResponse:
        """Filter agents using query string

        API Endpoint: GET /api/v2/agents?query=[query]

        Args:
            query (str, required): Query string for filtering agents
            page (int, optional): Page number
            per_page (int, optional): Number of entries per page

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            agents = await ds.filter_agents(query="email:'john@example.com'")
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/agents?query={query}"
        params = {}
        if page is not None:
            params['page'] = page
        if per_page is not None:
            params['per_page'] = per_page
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"filter_agents: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed filter_agents" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in filter_agents: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute filter_agents"
            )

    async def update_agent(
        self,
        id: int,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        occasional: Optional[bool] = None,
        job_title: Optional[str] = None,
        email: Optional[str] = None,
        work_phone_number: Optional[str] = None,
        mobile_phone_number: Optional[str] = None,
        department_ids: Optional[List[int]] = None,
        can_see_all_tickets_from_associated_departments: Optional[bool] = None,
        reporting_manager_id: Optional[int] = None,
        address: Optional[str] = None,
        time_zone: Optional[str] = None,
        time_format: Optional[str] = None,
        language: Optional[str] = None,
        location_id: Optional[int] = None,
        background_information: Optional[str] = None,
        scoreboard_level_id: Optional[int] = None,
        roles: Optional[List[Dict[str, Any]]] = None,
        signature: Optional[str] = None,
        custom_fields: Optional[Dict[str, Any]] = None
    ) -> FreshDeskResponse:
        """Update an existing agent

        API Endpoint: PUT /api/v2/agents/[id]

        Args:
            id (int, required): ID of the agent
            first_name (str, optional): First name of the agent
            last_name (str, optional): Last name of the agent
            occasional (bool, optional): True if occasional agent
            job_title (str, optional): Job title
            email (str, optional): Email address
            work_phone_number (str, optional): Work phone number
            mobile_phone_number (str, optional): Mobile phone number
            department_ids (List[int], optional): Department IDs
            can_see_all_tickets_from_associated_departments (bool, optional): Can view all department tickets
            reporting_manager_id (int, optional): Reporting manager ID
            address (str, optional): Address
            time_zone (str, optional): Time zone
            time_format (str, optional): Time format
            language (str, optional): Language code
            location_id (int, optional): Location ID
            background_information (str, optional): Background information
            scoreboard_level_id (int, optional): Scoreboard level ID
            roles (List[Dict[str, Any]], optional): Array of role objects
            signature (str, optional): Signature
            custom_fields (Dict[str, Any], optional): Custom fields

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            agent = await ds.update_agent(id=123, job_title="Senior Engineer")
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/agents/{id}"
        request_body: Dict[str, Any] = {}
        if first_name is not None:
            request_body['first_name'] = first_name
        if last_name is not None:
            request_body['last_name'] = last_name
        if occasional is not None:
            request_body['occasional'] = occasional
        if job_title is not None:
            request_body['job_title'] = job_title
        if email is not None:
            request_body['email'] = email
        if work_phone_number is not None:
            request_body['work_phone_number'] = work_phone_number
        if mobile_phone_number is not None:
            request_body['mobile_phone_number'] = mobile_phone_number
        if department_ids is not None:
            request_body['department_ids'] = department_ids
        if can_see_all_tickets_from_associated_departments is not None:
            request_body['can_see_all_tickets_from_associated_departments'] = can_see_all_tickets_from_associated_departments
        if reporting_manager_id is not None:
            request_body['reporting_manager_id'] = reporting_manager_id
        if address is not None:
            request_body['address'] = address
        if time_zone is not None:
            request_body['time_zone'] = time_zone
        if time_format is not None:
            request_body['time_format'] = time_format
        if language is not None:
            request_body['language'] = language
        if location_id is not None:
            request_body['location_id'] = location_id
        if background_information is not None:
            request_body['background_information'] = background_information
        if scoreboard_level_id is not None:
            request_body['scoreboard_level_id'] = scoreboard_level_id
        if roles is not None:
            request_body['roles'] = roles
        if signature is not None:
            request_body['signature'] = signature
        if custom_fields is not None:
            request_body['custom_fields'] = custom_fields

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"update_agent: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed update_agent" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in update_agent: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute update_agent"
            )

    async def deactivate_agent(
        self,
        id: int
    ) -> FreshDeskResponse:
        """Deactivate an agent

        API Endpoint: DELETE /api/v2/agents/[id]

        Args:
            id (int, required): ID of the agent

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            await ds.deactivate_agent(id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/agents/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"deactivate_agent: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed deactivate_agent" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in deactivate_agent: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute deactivate_agent"
            )

    async def forget_agent(
        self,
        id: int
    ) -> FreshDeskResponse:
        """Permanently delete an agent and their tickets

        API Endpoint: DELETE /api/v2/agents/[id]/forget

        Args:
            id (int, required): ID of the agent

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            await ds.forget_agent(id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/agents/{id}/forget"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"forget_agent: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed forget_agent" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in forget_agent: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute forget_agent"
            )

    async def reactivate_agent(
        self,
        id: int
    ) -> FreshDeskResponse:
        """Reactivate a deactivated agent

        API Endpoint: PUT /api/v2/agents/[id]/reactivate

        Args:
            id (int, required): ID of the agent

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            agent = await ds.reactivate_agent(id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/agents/{id}/reactivate"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"reactivate_agent: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed reactivate_agent" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in reactivate_agent: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute reactivate_agent"
            )

    async def list_agent_fields(
        self,
        include: Optional[str] = None
    ) -> FreshDeskResponse:
        """List all built-in and custom fields for agents

        API Endpoint: GET /api/v2/agent_fields

        Args:
            include (str, optional): Include additional details (e.g., 'user_field_groups')

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            fields = await ds.list_agent_fields()
        """
        url = self._freshdesk_client.get_base_url()
        url += "/agent_fields"
        params = {}
        if include is not None:
            params['include'] = include
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_agent_fields: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_agent_fields" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_agent_fields: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_agent_fields"
            )

    async def create_software(
        self,
        name: str,
        description: Optional[str] = None,
        application_type: Optional[str] = "desktop",
        status: Optional[str] = None,
        publisher_id: Optional[int] = None,
        managed_by_id: Optional[int] = None,
        notes: Optional[str] = None,
        category: Optional[str] = None,
        source: Optional[str] = None,
        workspace_id: Optional[int] = None
    ) -> FreshDeskResponse:
        """Create a new software/application in FreshDesk

        API Endpoint: POST /api/v2/applications

        Args:
            name (str, required): Name of the software
            description (str, optional): Description of the software
            application_type (str, optional): Type of application (desktop/saas/mobile)
            status (str, optional): Status (blacklisted/ignored/managed)
            publisher_id (int, optional): ID of the Vendor/Publisher
            managed_by_id (int, optional): ID of the user managing the software
            notes (str, optional): Notes about the software
            category (str, optional): Category of the software
            source (str, optional): Source of software details (API, Okta, Google, etc.)
            workspace_id (int, optional): Workspace ID

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            software = await ds.create_software(name="FreshDesk", application_type="saas")
        """
        url = self._freshdesk_client.get_base_url()
        url += "/applications"
        request_body: Dict[str, Any] = {}
        if name is not None:
            request_body['name'] = name
        if description is not None:
            request_body['description'] = description
        if application_type is not None:
            request_body['application_type'] = application_type
        if status is not None:
            request_body['status'] = status
        if publisher_id is not None:
            request_body['publisher_id'] = publisher_id
        if managed_by_id is not None:
            request_body['managed_by_id'] = managed_by_id
        if notes is not None:
            request_body['notes'] = notes
        if category is not None:
            request_body['category'] = category
        if source is not None:
            request_body['source'] = source
        if workspace_id is not None:
            request_body['workspace_id'] = workspace_id

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"create_software: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed create_software" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in create_software: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute create_software"
            )

    async def view_software(
        self,
        id: int
    ) -> FreshDeskResponse:
        """View a specific software/application by ID

        API Endpoint: GET /api/v2/applications/[id]

        Args:
            id (int, required): ID of the software

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            software = await ds.view_software(id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"view_software: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed view_software" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in view_software: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute view_software"
            )

    async def list_software(
        self,
        workspace_id: Optional[int] = None,
        page: Optional[int] = 1,
        per_page: Optional[int] = 30
    ) -> FreshDeskResponse:
        """List all software/applications

        API Endpoint: GET /api/v2/applications

        Args:
            workspace_id (int, optional): Workspace ID (0 for all workspaces)
            page (int, optional): Page number
            per_page (int, optional): Number of entries per page

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            software_list = await ds.list_software()
        """
        url = self._freshdesk_client.get_base_url()
        url += "/applications"
        params = {}
        if workspace_id is not None:
            params['workspace_id'] = workspace_id
        if page is not None:
            params['page'] = page
        if per_page is not None:
            params['per_page'] = per_page
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_software: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_software" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_software: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_software"
            )

    async def update_software(
        self,
        id: int,
        name: Optional[str] = None,
        description: Optional[str] = None,
        application_type: Optional[str] = None,
        status: Optional[str] = None,
        publisher_id: Optional[int] = None,
        managed_by_id: Optional[int] = None,
        notes: Optional[str] = None,
        category: Optional[str] = None,
        source: Optional[str] = None
    ) -> FreshDeskResponse:
        """Update an existing software/application

        API Endpoint: PUT /api/v2/applications/[id]

        Args:
            id (int, required): ID of the software
            name (str, optional): Name of the software
            description (str, optional): Description of the software
            application_type (str, optional): Type of application (desktop/saas/mobile)
            status (str, optional): Status (blacklisted/ignored/managed)
            publisher_id (int, optional): ID of the Vendor/Publisher
            managed_by_id (int, optional): ID of the user managing the software
            notes (str, optional): Notes about the software
            category (str, optional): Category of the software
            source (str, optional): Source of software details

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            software = await ds.update_software(id=123, status="managed")
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}"
        request_body: Dict[str, Any] = {}
        if name is not None:
            request_body['name'] = name
        if description is not None:
            request_body['description'] = description
        if application_type is not None:
            request_body['application_type'] = application_type
        if status is not None:
            request_body['status'] = status
        if publisher_id is not None:
            request_body['publisher_id'] = publisher_id
        if managed_by_id is not None:
            request_body['managed_by_id'] = managed_by_id
        if notes is not None:
            request_body['notes'] = notes
        if category is not None:
            request_body['category'] = category
        if source is not None:
            request_body['source'] = source

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"update_software: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed update_software" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in update_software: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute update_software"
            )

    async def delete_software(
        self,
        id: int
    ) -> FreshDeskResponse:
        """Delete a specific software/application

        API Endpoint: DELETE /api/v2/applications/[id]

        Args:
            id (int, required): ID of the software to delete

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            await ds.delete_software(id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"delete_software: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed delete_software" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in delete_software: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute delete_software"
            )

    async def list_software_licenses(
        self,
        id: int
    ) -> FreshDeskResponse:
        """List all licenses of a software

        API Endpoint: GET /api/v2/applications/[id]/licenses

        Args:
            id (int, required): ID of the software

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            licenses = await ds.list_software_licenses(id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}/licenses"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_software_licenses: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_software_licenses" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_software_licenses: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_software_licenses"
            )

    async def add_software_users(
        self,
        id: int,
        application_users: List[Dict[str, Any]]
    ) -> FreshDeskResponse:
        """Add users to a software in bulk

        API Endpoint: POST /api/v2/applications/[id]/users

        Args:
            id (int, required): ID of the software
            application_users (List[Dict[str, Any]], required): List of application user objects

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            users = await ds.add_software_users(id=123, application_users=[{"user_id": 456}])
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}/users"
        request_body: Dict[str, Any] = {}
        if application_users is not None:
            request_body['application_users'] = application_users

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"add_software_users: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed add_software_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in add_software_users: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute add_software_users"
            )

    async def view_software_user(
        self,
        id: int,
        user_id: int
    ) -> FreshDeskResponse:
        """View a specific user of a software

        API Endpoint: GET /api/v2/applications/[id]/users/[user_id]

        Args:
            id (int, required): ID of the software
            user_id (int, required): ID of the application user

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            user = await ds.view_software_user(id=123, user_id=456)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}/users/{user_id}"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"view_software_user: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed view_software_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in view_software_user: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute view_software_user"
            )

    async def list_software_users(
        self,
        id: int,
        page: Optional[int] = 1,
        per_page: Optional[int] = 30
    ) -> FreshDeskResponse:
        """List all users of a software

        API Endpoint: GET /api/v2/applications/[id]/users

        Args:
            id (int, required): ID of the software
            page (int, optional): Page number
            per_page (int, optional): Number of entries per page

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            users = await ds.list_software_users(id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}/users"
        params = {}
        if page is not None:
            params['page'] = page
        if per_page is not None:
            params['per_page'] = per_page
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_software_users: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_software_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_software_users: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_software_users"
            )

    async def update_software_users(
        self,
        id: int,
        application_users: List[Dict[str, Any]]
    ) -> FreshDeskResponse:
        """Update users of a software in bulk

        API Endpoint: PUT /api/v2/applications/[id]/users

        Args:
            id (int, required): ID of the software
            application_users (List[Dict[str, Any]], required): List of application user objects to update

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            users = await ds.update_software_users(id=123, application_users=[{"user_id": 456, "license_id": 10}])
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}/users"
        request_body: Dict[str, Any] = {}
        if application_users is not None:
            request_body['application_users'] = application_users

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"update_software_users: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed update_software_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in update_software_users: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute update_software_users"
            )

    async def remove_software_users(
        self,
        id: int,
        user_ids: List[int]
    ) -> FreshDeskResponse:
        """Remove users from a software in bulk

        API Endpoint: DELETE /api/v2/applications/[id]/users

        Args:
            id (int, required): ID of the software
            user_ids (List[int], required): List of user IDs to remove

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            await ds.remove_software_users(id=123, user_ids=[456, 789])
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}/users"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"remove_software_users: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed remove_software_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in remove_software_users: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute remove_software_users"
            )

    async def add_software_installation(
        self,
        id: int,
        installation_machine_id: int,
        installation_path: Optional[str] = None,
        version: Optional[str] = None,
        installation_date: Optional[str] = None
    ) -> FreshDeskResponse:
        """Add a device installation to a software

        API Endpoint: POST /api/v2/applications/[id]/installations

        Args:
            id (int, required): ID of the software
            installation_machine_id (int, required): Display ID of device
            installation_path (str, optional): Path where software is installed
            version (str, optional): Version of installed software
            installation_date (str, optional): Installation date (ISO format)

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            installation = await ds.add_software_installation(id=123, installation_machine_id=456)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}/installations"
        request_body: Dict[str, Any] = {}
        if installation_machine_id is not None:
            request_body['installation_machine_id'] = installation_machine_id
        if installation_path is not None:
            request_body['installation_path'] = installation_path
        if version is not None:
            request_body['version'] = version
        if installation_date is not None:
            request_body['installation_date'] = installation_date

        try:
            request = HTTPRequest(
                url=url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"add_software_installation: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed add_software_installation" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in add_software_installation: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute add_software_installation"
            )

    async def list_software_installations(
        self,
        id: int,
        page: Optional[int] = 1,
        per_page: Optional[int] = 30
    ) -> FreshDeskResponse:
        """List all installations of a software

        API Endpoint: GET /api/v2/applications/[id]/installations

        Args:
            id (int, required): ID of the software
            page (int, optional): Page number
            per_page (int, optional): Number of entries per page

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            installations = await ds.list_software_installations(id=123)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}/installations"
        params = {}
        if page is not None:
            params['page'] = page
        if per_page is not None:
            params['per_page'] = per_page
        if params:
            from urllib.parse import urlencode
            url += '?' + urlencode(params)
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="GET",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"list_software_installations: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed list_software_installations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in list_software_installations: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute list_software_installations"
            )

    async def remove_software_installations(
        self,
        id: int,
        device_ids: List[int]
    ) -> FreshDeskResponse:
        """Remove device installations from a software in bulk

        API Endpoint: DELETE /api/v2/applications/[id]/installations

        Args:
            id (int, required): ID of the software
            device_ids (List[int], required): List of device display IDs to remove

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            await ds.remove_software_installations(id=123, device_ids=[456, 789])
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}/installations"
        request_body = None

        try:
            request = HTTPRequest(
                url=url,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"remove_software_installations: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed remove_software_installations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in remove_software_installations: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute remove_software_installations"
            )

    async def move_software(
        self,
        id: int,
        workspace_id: int
    ) -> FreshDeskResponse:
        """Move software to a different workspace

        API Endpoint: PUT /api/v2/applications/[id]/move_workspace

        Args:
            id (int, required): ID of the software
            workspace_id (int, required): ID of the target workspace

        Returns:
            FreshDeskResponse: Standardized response wrapper

        Example:
            software = await ds.move_software(id=123, workspace_id=2)
        """
        url = self._freshdesk_client.get_base_url()
        url += f"/applications/{id}/move_workspace"
        request_body: Dict[str, Any] = {}
        if workspace_id is not None:
            request_body['workspace_id'] = workspace_id

        try:
            request = HTTPRequest(
                url=url,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"move_software: Status={response.status}, Response={response_text[:200] if response_text else 'Empty'}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message="Successfully executed move_software" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            logger.debug(f"Error in move_software: {e}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message="Failed to execute move_software"
            )

