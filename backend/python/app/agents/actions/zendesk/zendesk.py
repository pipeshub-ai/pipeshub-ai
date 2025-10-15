import asyncio
import concurrent.futures
import json
import logging
from typing import Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.zendesk.zendesk import ZendeskClient
from app.sources.external.zendesk.zendesk import ZendeskDataSource

logger = logging.getLogger(__name__)


class Zendesk:
    """Zendesk tool exposed to the agents"""
    def __init__(self, client: ZendeskClient) -> None:
        """Initialize the Zendesk tool"""
        """
        Args:
            client: Zendesk client object
        Returns:
            None
        """
        self.client = ZendeskDataSource(client)

    def _run_async(self, coro) -> HTTPResponse: # type: ignore [valid method]
        """Helper method to run async operations in sync context"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If we're already in an async context, we need to use a thread pool
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, coro)
                    return future.result()
            else:
                return loop.run_until_complete(coro)
        except Exception as e:
            logger.error(f"Error running async operation: {e}")
            raise

    @tool(
        app_name="zendesk",
        tool_name="get_current_user",
        description="Get current user information",
        parameters=[]
    )
    def get_current_user(self) -> Tuple[bool, str]:
        """Get current user information"""
        """
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use ZendeskDataSource method
            response = self._run_async(self.client.show_current_user())

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting current user: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zendesk",
        tool_name="list_tickets",
        description="List tickets",
        parameters=[
            ToolParameter(
                name="sort_by",
                type=ParameterType.STRING,
                description="Field to sort by",
                required=False
            ),
            ToolParameter(
                name="sort_order",
                type=ParameterType.STRING,
                description="Sort order (asc or desc)",
                required=False
            ),
            ToolParameter(
                name="per_page",
                type=ParameterType.INTEGER,
                description="Number of tickets per page",
                required=False
            ),
            ToolParameter(
                name="page",
                type=ParameterType.INTEGER,
                description="Page number",
                required=False
            )
        ]
    )
    def list_tickets(
        self,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None,
        per_page: Optional[int] = None,
        page: Optional[int] = None
    ) -> Tuple[bool, str]:
        """List tickets"""
        """
        Args:
            sort_by: Field to sort by
            sort_order: Sort order (asc or desc)
            per_page: Number of tickets per page
            page: Page number
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use ZendeskDataSource method
            response = self._run_async(self.client.list_tickets(
                sort_by=sort_by,
                sort_order=sort_order,
                per_page=per_page,
                page=page
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error listing tickets: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zendesk",
        tool_name="get_ticket",
        description="Get a specific ticket",
        parameters=[
            ToolParameter(
                name="ticket_id",
                type=ParameterType.STRING,
                description="ID of the ticket to get",
                required=True
            )
        ]
    )
    def get_ticket(self, ticket_id: str) -> Tuple[bool, str]:
        """Get a specific ticket"""
        """
        Args:
            ticket_id: ID of the ticket to get
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use ZendeskDataSource method (coerce ID to int)
            tid = int(ticket_id)
            response = self._run_async(self.client.show_ticket(ticket_id=tid))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting ticket: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zendesk",
        tool_name="create_ticket",
        description="Create a new ticket",
        parameters=[
            ToolParameter(
                name="subject",
                type=ParameterType.STRING,
                description="Subject of the ticket",
                required=True
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="Description of the ticket",
                required=True
            ),
            ToolParameter(
                name="requester_id",
                type=ParameterType.STRING,
                description="ID of the requester",
                required=False
            ),
            ToolParameter(
                name="assignee_id",
                type=ParameterType.STRING,
                description="ID of the assignee",
                required=False
            ),
            ToolParameter(
                name="priority",
                type=ParameterType.STRING,
                description="Priority of the ticket",
                required=False
            ),
            ToolParameter(
                name="status",
                type=ParameterType.STRING,
                description="Status of the ticket",
                required=False
            )
        ]
    )
    def create_ticket(
        self,
        subject: str,
        description: str,
        requester_id: Optional[str] = None,
        assignee_id: Optional[str] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Create a new ticket"""
        """
        Args:
            subject: Subject of the ticket
            description: Description of the ticket
            requester_id: ID of the requester
            assignee_id: ID of the assignee
            priority: Priority of the ticket
            status: Status of the ticket
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Map to data source flat params; description -> comment body
            response = self._run_async(self.client.create_ticket(
                subject=subject,
                comment={"body": description},
                requester_id=int(requester_id) if requester_id is not None else None,
                assignee_id=int(assignee_id) if assignee_id is not None else None,
                priority=priority,
                status=status
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error creating ticket: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zendesk",
        tool_name="update_ticket",
        description="Update a ticket",
        parameters=[
            ToolParameter(
                name="ticket_id",
                type=ParameterType.STRING,
                description="ID of the ticket to update",
                required=True
            ),
            ToolParameter(
                name="subject",
                type=ParameterType.STRING,
                description="New subject of the ticket",
                required=False
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="New description of the ticket",
                required=False
            ),
            ToolParameter(
                name="assignee_id",
                type=ParameterType.STRING,
                description="New assignee ID",
                required=False
            ),
            ToolParameter(
                name="priority",
                type=ParameterType.STRING,
                description="New priority of the ticket",
                required=False
            ),
            ToolParameter(
                name="status",
                type=ParameterType.STRING,
                description="New status of the ticket",
                required=False
            )
        ]
    )
    def update_ticket(
        self,
        ticket_id: str,
        subject: Optional[str] = None,
        description: Optional[str] = None,
        assignee_id: Optional[str] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Update a ticket"""
        """
        Args:
            ticket_id: ID of the ticket to update
            subject: New subject of the ticket
            description: New description of the ticket
            assignee_id: New assignee ID
            priority: New priority of the ticket
            status: New status of the ticket
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use ZendeskDataSource method with flat params; description -> comment body
            tid = int(ticket_id)
            response = self._run_async(self.client.update_ticket(
                ticket_id=tid,
                subject=subject,
                comment={"body": description} if description is not None else None,
                assignee_id=int(assignee_id) if assignee_id is not None else None,
                priority=priority,
                status=status
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error updating ticket: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zendesk",
        tool_name="delete_ticket",
        description="Delete a ticket",
        parameters=[
            ToolParameter(
                name="ticket_id",
                type=ParameterType.STRING,
                description="ID of the ticket to delete",
                required=True
            )
        ]
    )
    def delete_ticket(self, ticket_id: str) -> Tuple[bool, str]:
        """Delete a ticket"""
        """
        Args:
            ticket_id: ID of the ticket to delete
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use ZendeskDataSource method (coerce ID to int)
            tid = int(ticket_id)
            response = self._run_async(self.client.delete_ticket(ticket_id=tid))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error deleting ticket: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zendesk",
        tool_name="list_users",
        description="List users",
        parameters=[
            ToolParameter(
                name="per_page",
                type=ParameterType.INTEGER,
                description="Number of users per page",
                required=False
            ),
            ToolParameter(
                name="page",
                type=ParameterType.INTEGER,
                description="Page number",
                required=False
            )
        ]
    )
    def list_users(
        self,
        role: Optional[str] = None,
        include: Optional[str] = None
    ) -> Tuple[bool, str]:
        """List users"""
        """
        Args:
            per_page: Number of users per page
            page: Page number
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use ZendeskDataSource method (supports role/include among others)
            response = self._run_async(self.client.list_users(
                role=role,
                include=include
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error listing users: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zendesk",
        tool_name="get_user",
        description="Get a specific user",
        parameters=[
            ToolParameter(
                name="user_id",
                type=ParameterType.STRING,
                description="ID of the user to get",
                required=True
            )
        ]
    )
    def get_user(self, user_id: str) -> Tuple[bool, str]:
        """Get a specific user"""
        """
        Args:
            user_id: ID of the user to get
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use ZendeskDataSource method (coerce ID to int)
            uid = int(user_id)
            response = self._run_async(self.client.show_user(user_id=uid))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error getting user: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="zendesk",
        tool_name="search_tickets",
        description="Search tickets",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query",
                required=True
            ),
            ToolParameter(
                name="sort_by",
                type=ParameterType.STRING,
                description="Field to sort by",
                required=False
            ),
            ToolParameter(
                name="sort_order",
                type=ParameterType.STRING,
                description="Sort order (asc or desc)",
                required=False
            ),
            ToolParameter(
                name="per_page",
                type=ParameterType.INTEGER,
                description="Number of results per page",
                required=False
            ),
            ToolParameter(
                name="page",
                type=ParameterType.INTEGER,
                description="Page number",
                required=False
            )
        ]
    )
    def search_tickets(
        self,
        query: str,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Search tickets"""
        """
        Args:
            query: Search query
            sort_by: Field to sort by
            sort_order: Sort order (asc or desc)
            per_page: Number of results per page
            page: Page number
        Returns:
            Tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use ZendeskDataSource method (generic search)
            response = self._run_async(self.client.search(
                query=query,
                sort_by=sort_by,
                sort_order=sort_order
            ))

            if response.success:
                return True, response.to_json()
            else:
                return False, response.to_json()
        except Exception as e:
            logger.error(f"Error searching tickets: {e}")
            return False, json.dumps({"error": str(e)})
