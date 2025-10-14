import asyncio
import json
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.freshdesk.freshdesk import FreshDeskClient, FreshDeskResponse
from app.sources.external.freshdesk.freshdesk import FreshDeskDataSource

logger = logging.getLogger(__name__)


class FreshDesk:
    """FreshDesk tools exposed to the agents using FreshDeskDataSource"""

    def __init__(self, client: FreshDeskClient) -> None:
        """Initialize the FreshDesk tool with a data source wrapper.
        Args:
            client: An initialized `FreshDeskClient` instance
        """
        self.client = FreshDeskDataSource(client)
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(
            target=self._start_background_loop,
            daemon=True
        )
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop."""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro: Any) -> FreshDeskResponse:
        """Run a coroutine safely from sync context via a dedicated loop."""
        future = asyncio.run_coroutine_threadsafe(coro, self._bg_loop)
        return future.result()

    def _handle_response(
        self,
        response: FreshDeskResponse,
        success_message: str
    ) -> Tuple[bool, str]:
        """Handle FreshDeskResponse and return standardized tuple."""
        if response.success:
            return True, json.dumps({
                "message": success_message,
                "data": response.data or {}
            })
        return False, json.dumps({
            "error": response.error or "Unknown error"
        })

    @tool(
        app_name="freshdesk",
        tool_name="create_ticket",
        description="Create a new support ticket in FreshDesk",
        parameters=[
            ToolParameter(
                name="subject",
                type=ParameterType.STRING,
                description="The subject/title of the ticket (required)"
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="The description/content of the ticket (required)"
            ),
            ToolParameter(
                name="email",
                type=ParameterType.STRING,
                description="The email address of the requester (required)"
            ),
            ToolParameter(
                name="priority",
                type=ParameterType.NUMBER,
                description="Priority level (1=Low, 2=Medium, 3=High, 4=Urgent)",
                required=False
            ),
            ToolParameter(
                name="status",
                type=ParameterType.NUMBER,
                description="Status (2=Open, 3=Pending, 4=Resolved, 5=Closed)",
                required=False
            ),
            ToolParameter(
                name="group_id",
                type=ParameterType.NUMBER,
                description="ID of the group to assign the ticket to",
                required=False
            ),
            ToolParameter(
                name="agent_id",
                type=ParameterType.NUMBER,
                description="ID of the agent to assign the ticket to",
                required=False
            )
        ],
        returns="JSON with created ticket details"
    )
    def create_ticket(
        self,
        subject: str,
        description: str,
        email: str,
        priority: Optional[int] = None,
        status: Optional[int] = None,
        group_id: Optional[int] = None,
        agent_id: Optional[int] = None
    ) -> Tuple[bool, str]:
        try:
            ticket_data = {
                "subject": subject,
                "description": description,
                "email": email,
                "priority": priority,
                "status": status,
                "group_id": group_id,
                "agent_id": agent_id
            }
            # Remove None values
            ticket_data = {k: v for k, v in ticket_data.items() if v is not None}

            response = self._run_async(self.client.create_ticket(**ticket_data))
            return self._handle_response(response, "Ticket created successfully")
        except Exception as e:
            logger.error(f"Error creating ticket: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="freshdesk",
        tool_name="get_ticket",
        description="Get details of a specific ticket",
        parameters=[
            ToolParameter(
                name="ticket_id",
                type=ParameterType.NUMBER,
                description="The ID of the ticket to retrieve (required)"
            )
        ],
        returns="JSON with ticket details"
    )
    def get_ticket(self, ticket_id: int) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.get_ticket(ticket_id=ticket_id))
            return self._handle_response(response, "Ticket retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting ticket: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="freshdesk",
        tool_name="update_ticket",
        description="Update an existing ticket",
        parameters=[
            ToolParameter(
                name="ticket_id",
                type=ParameterType.NUMBER,
                description="The ID of the ticket to update (required)"
            ),
            ToolParameter(
                name="subject",
                type=ParameterType.STRING,
                description="Updated subject/title",
                required=False
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="Updated description/content",
                required=False
            ),
            ToolParameter(
                name="priority",
                type=ParameterType.NUMBER,
                description="Updated priority level (1=Low, 2=Medium, 3=High, 4=Urgent)",
                required=False
            ),
            ToolParameter(
                name="status",
                type=ParameterType.NUMBER,
                description="Updated status (2=Open, 3=Pending, 4=Resolved, 5=Closed)",
                required=False
            ),
            ToolParameter(
                name="group_id",
                type=ParameterType.NUMBER,
                description="Updated group ID",
                required=False
            ),
            ToolParameter(
                name="agent_id",
                type=ParameterType.NUMBER,
                description="Updated agent ID",
                required=False
            )
        ],
        returns="JSON with updated ticket details"
    )
    def update_ticket(
        self,
        ticket_id: int,
        subject: Optional[str] = None,
        description: Optional[str] = None,
        priority: Optional[int] = None,
        status: Optional[int] = None,
        group_id: Optional[int] = None,
        agent_id: Optional[int] = None
    ) -> Tuple[bool, str]:
        try:
            update_data = {
                "subject": subject,
                "description": description,
                "priority": priority,
                "status": status,
                "group_id": group_id,
                "agent_id": agent_id
            }
            # Remove None values
            update_data = {k: v for k, v in update_data.items() if v is not None}

            response = self._run_async(
                self.client.update_ticket(ticket_id=ticket_id, **update_data)
            )
            return self._handle_response(response, "Ticket updated successfully")
        except Exception as e:
            logger.error(f"Error updating ticket: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="freshdesk",
        tool_name="delete_ticket",
        description="Delete a ticket (permanently removes it)",
        parameters=[
            ToolParameter(
                name="ticket_id",
                type=ParameterType.NUMBER,
                description="The ID of the ticket to delete (required)"
            )
        ],
        returns="JSON with deletion confirmation"
    )
    def delete_ticket(self, ticket_id: int) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.delete_ticket(ticket_id=ticket_id))
            return self._handle_response(response, "Ticket deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting ticket: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="freshdesk",
        tool_name="create_note",
        description="Add a note to an existing ticket",
        parameters=[
            ToolParameter(
                name="ticket_id",
                type=ParameterType.NUMBER,
                description="The ID of the ticket to add a note to (required)"
            ),
            ToolParameter(
                name="body",
                type=ParameterType.STRING,
                description="The content of the note (required)"
            ),
            ToolParameter(
                name="private",
                type=ParameterType.BOOLEAN,
                description="Whether the note should be private (only visible to agents)",
                required=False
            )
        ],
        returns="JSON with created note details"
    )
    def create_note(
        self,
        ticket_id: int,
        body: str,
        private: Optional[bool] = None
    ) -> Tuple[bool, str]:
        try:
            note_data = {
                "body": body,
                "private": private
            }
            # Remove None values
            note_data = {k: v for k, v in note_data.items() if v is not None}

            response = self._run_async(
                self.client.create_note(ticket_id=ticket_id, **note_data)
            )
            return self._handle_response(response, "Note created successfully")
        except Exception as e:
            logger.error(f"Error creating note: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="freshdesk",
        tool_name="create_reply",
        description="Add a reply to an existing ticket",
        parameters=[
            ToolParameter(
                name="ticket_id",
                type=ParameterType.NUMBER,
                description="The ID of the ticket to reply to (required)"
            ),
            ToolParameter(
                name="body",
                type=ParameterType.STRING,
                description="The content of the reply (required)"
            ),
            ToolParameter(
                name="cc_emails",
                type=ParameterType.STRING,
                description="Comma-separated list of email addresses to CC",
                required=False
            ),
            ToolParameter(
                name="attachments",
                type=ParameterType.ARRAY,
                description="Array of attachment objects",
                required=False
            )
        ],
        returns="JSON with created reply details"
    )
    def create_reply(
        self,
        ticket_id: int,
        body: str,
        cc_emails: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, str]:
        try:
            reply_data = {
                "body": body,
                "cc_emails": cc_emails,
                "attachments": attachments
            }
            # Remove None values
            reply_data = {k: v for k, v in reply_data.items() if v is not None}

            response = self._run_async(
                self.client.create_reply(ticket_id=ticket_id, **reply_data)
            )
            return self._handle_response(response, "Reply created successfully")
        except Exception as e:
            logger.error(f"Error creating reply: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="freshdesk",
        tool_name="create_agent",
        description="Create a new agent in FreshDesk",
        parameters=[
            ToolParameter(
                name="email",
                type=ParameterType.STRING,
                description="The email address of the agent (required)"
            ),
            ToolParameter(
                name="first_name",
                type=ParameterType.STRING,
                description="The first name of the agent (required)"
            ),
            ToolParameter(
                name="last_name",
                type=ParameterType.STRING,
                description="The last name of the agent (required)"
            ),
            ToolParameter(
                name="role",
                type=ParameterType.STRING,
                description="The role of the agent (admin, agent, supervisor)",
                required=False
            ),
            ToolParameter(
                name="group_ids",
                type=ParameterType.ARRAY,
                description="Array of group IDs to assign the agent to",
                required=False
            )
        ],
        returns="JSON with created agent details"
    )
    def create_agent(
        self,
        email: str,
        first_name: str,
        last_name: str,
        role: Optional[str] = None,
        group_ids: Optional[List[int]] = None
    ) -> Tuple[bool, str]:
        try:
            agent_data = {
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "role": role,
                "group_ids": group_ids
            }
            # Remove None values
            agent_data = {k: v for k, v in agent_data.items() if v is not None}

            response = self._run_async(self.client.create_agent(**agent_data))
            return self._handle_response(response, "Agent created successfully")
        except Exception as e:
            logger.error(f"Error creating agent: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="freshdesk",
        tool_name="search_tickets",
        description="Search for tickets with various filters",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query string (required)"
            ),
            ToolParameter(
                name="page",
                type=ParameterType.NUMBER,
                description="Page number for pagination",
                required=False
            ),
            ToolParameter(
                name="per_page",
                type=ParameterType.NUMBER,
                description="Number of results per page (max 100)",
                required=False
            )
        ],
        returns="JSON with search results"
    )
    def search_tickets(
        self,
        query: str,
        page: Optional[int] = None,
        per_page: Optional[int] = None
    ) -> Tuple[bool, str]:
        try:
            search_params = {
                "query": query,
                "page": page,
                "per_page": per_page
            }
            # Remove None values
            search_params = {k: v for k, v in search_params.items() if v is not None}

            response = self._run_async(self.client.search_tickets(**search_params))
            return self._handle_response(response, "Ticket search completed successfully")
        except Exception as e:
            logger.error(f"Error searching tickets: {e}")
            return False, json.dumps({"error": str(e)})
