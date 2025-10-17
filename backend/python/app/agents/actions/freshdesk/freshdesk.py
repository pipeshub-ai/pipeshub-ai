import asyncio
import json
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.freshdesk.freshdesk import FreshDeskClient, FreshDeskResponse
from app.sources.external.freshdesk.freshdesk import FreshdeskDataSource

logger = logging.getLogger(__name__)


class FreshDesk:
    """FreshDesk tools exposed to the agents using FreshdeskDataSource"""

    def __init__(self, client: FreshDeskClient) -> None:
        """Initialize the Freshdesk tool with a data source wrapper.
        Args:
            client: An initialized `FreshDeskClient` instance
        """
        self.client = FreshdeskDataSource(client)
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

    def _run_async(self, coro) -> FreshDeskResponse:
        """Run a coroutine safely from sync context via a dedicated loop."""
        future = asyncio.run_coroutine_threadsafe(coro, self._bg_loop)
        return future.result()

    def shutdown(self) -> None:
        """Gracefully stop the background event loop and thread."""
        try:
            if getattr(self, "_bg_loop", None) is not None and self._bg_loop.is_running():
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            if getattr(self, "_bg_loop_thread", None) is not None:
                self._bg_loop_thread.join()
            if getattr(self, "_bg_loop", None) is not None:
                self._bg_loop.close()
        except Exception as exc:
            logger.warning(f"FreshDesk shutdown encountered an issue: {exc}")

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
                name="requester_id",
                type=ParameterType.NUMBER,
                description="User ID of the requester",
                required=False
            ),
            ToolParameter(
                name="phone",
                type=ParameterType.STRING,
                description="Phone number of the requester",
                required=False
            ),
            ToolParameter(
                name="source",
                type=ParameterType.NUMBER,
                description="Source of the ticket",
                required=False
            ),
            ToolParameter(
                name="tags",
                type=ParameterType.ARRAY,
                description="Tags associated with the ticket (array of strings)",
                required=False
            ),
            ToolParameter(
                name="cc_emails",
                type=ParameterType.ARRAY,
                description="CC email addresses (array of strings)",
                required=False
            ),
            ToolParameter(
                name="custom_fields",
                type=ParameterType.OBJECT,
                description="Custom field values (object)",
                required=False
            ),
            ToolParameter(
                name="attachments",
                type=ParameterType.ARRAY,
                description="File paths for attachments (array of strings)",
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
        requester_id: Optional[int] = None,
        phone: Optional[str] = None,
        source: Optional[int] = None,
        tags: Optional[List[str]] = None,
        cc_emails: Optional[List[str]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        attachments: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        try:
            ticket_data = {
                "subject": subject,
                "description": description,
                "email": email,
                "priority": priority,
                "status": status,
                "requester_id": requester_id,
                "phone": phone,
                "source": source,
                "tags": tags,
                "cc_emails": cc_emails,
                "custom_fields": custom_fields,
                "attachments": attachments
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
    def get_ticket(self, ticket_id: int, include: Optional[str] = None) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.get_ticket(id=ticket_id, include=include))
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
                name="tags",
                type=ParameterType.ARRAY,
                description="Tags to associate (array of strings)",
                required=False
            ),
            ToolParameter(
                name="custom_fields",
                type=ParameterType.OBJECT,
                description="Custom field values (object)",
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
        tags: Optional[List[str]] = None,
        custom_fields: Optional[Dict[str, Any]] = None
    ) -> Tuple[bool, str]:
        try:
            update_data = {
                "subject": subject,
                "description": description,
                "priority": priority,
                "status": status,
                "tags": tags,
                "custom_fields": custom_fields
            }
            # Remove None values
            update_data = {k: v for k, v in update_data.items() if v is not None}

            response = self._run_async(
                self.client.update_ticket(id=ticket_id, **update_data)
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
            response = self._run_async(self.client.delete_ticket(id=ticket_id))
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
            ),
            ToolParameter(
                name="notify_emails",
                type=ParameterType.ARRAY,
                description="Email addresses to notify (array of strings)",
                required=False
            )
        ],
        returns="JSON with created note details"
    )
    def create_note(
        self,
        ticket_id: int,
        body: str,
        private: Optional[bool] = None,
        notify_emails: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        try:
            note_data = {
                "body": body,
                "private": private,
                "notify_emails": notify_emails
            }
            # Remove None values
            note_data = {k: v for k, v in note_data.items() if v is not None}

            response = self._run_async(
                self.client.create_note(id=ticket_id, **note_data)
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
                type=ParameterType.ARRAY,
                description="CC email addresses (array of strings)",
                required=False
            ),
            ToolParameter(
                name="bcc_emails",
                type=ParameterType.ARRAY,
                description="BCC email addresses (array of strings)",
                required=False
            )
        ],
        returns="JSON with created reply details"
    )
    def create_reply(
        self,
        ticket_id: int,
        body: str,
        cc_emails: Optional[List[str]] = None,
        bcc_emails: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        try:
            reply_data = {
                "body": body,
                "cc_emails": cc_emails,
                "bcc_emails": bcc_emails
            }
            # Remove None values
            reply_data = {k: v for k, v in reply_data.items() if v is not None}

            response = self._run_async(
                self.client.create_reply(id=ticket_id, **reply_data)
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
                name="first_name",
                type=ParameterType.STRING,
                description="The first name of the agent (required)"
            ),
            ToolParameter(
                name="email",
                type=ParameterType.STRING,
                description="The email address of the agent (required)"
            ),
            ToolParameter(
                name="last_name",
                type=ParameterType.STRING,
                description="The last name of the agent",
                required=False
            ),
            ToolParameter(name="occasional", type=ParameterType.BOOLEAN, description="True if occasional agent", required=False),
            ToolParameter(name="job_title", type=ParameterType.STRING, description="Job title of the agent", required=False),
            ToolParameter(name="work_phone_number", type=ParameterType.STRING, description="Work phone number", required=False),
            ToolParameter(name="mobile_phone_number", type=ParameterType.STRING, description="Mobile phone number", required=False),
            ToolParameter(name="department_ids", type=ParameterType.ARRAY, description="IDs of departments (array of numbers)", required=False),
            ToolParameter(name="can_see_all_tickets_from_associated_departments", type=ParameterType.BOOLEAN, description="Can view all department tickets", required=False),
            ToolParameter(name="reporting_manager_id", type=ParameterType.NUMBER, description="User ID of reporting manager", required=False),
            ToolParameter(name="address", type=ParameterType.STRING, description="Address of the agent", required=False),
            ToolParameter(name="time_zone", type=ParameterType.STRING, description="Time zone", required=False),
            ToolParameter(name="time_format", type=ParameterType.STRING, description="Time format (12h or 24h)", required=False),
            ToolParameter(name="language", type=ParameterType.STRING, description="Language code", required=False),
            ToolParameter(name="location_id", type=ParameterType.NUMBER, description="Location ID", required=False),
            ToolParameter(name="background_information", type=ParameterType.STRING, description="Background information", required=False),
            ToolParameter(name="scoreboard_level_id", type=ParameterType.NUMBER, description="Scoreboard level ID", required=False),
            ToolParameter(name="roles", type=ParameterType.ARRAY, description="Array of role objects", required=False),
            ToolParameter(name="signature", type=ParameterType.STRING, description="Signature in HTML format", required=False),
            ToolParameter(name="custom_fields", type=ParameterType.OBJECT, description="Custom field values", required=False),
            ToolParameter(name="workspace_ids", type=ParameterType.ARRAY, description="Workspace IDs (array of numbers)", required=False)
        ],
        returns="JSON with created agent details"
    )
    def create_agent(
        self,
        first_name: str,
        email: str,
        last_name: Optional[str] = None,
        occasional: Optional[bool] = None,
        job_title: Optional[str] = None,
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
        custom_fields: Optional[Dict[str, Any]] = None,
        workspace_ids: Optional[List[int]] = None
    ) -> Tuple[bool, str]:
        try:
            agent_data = {
                "first_name": first_name,
                "email": email,
                "last_name": last_name,
                "occasional": occasional,
                "job_title": job_title,
                "work_phone_number": work_phone_number,
                "mobile_phone_number": mobile_phone_number,
                "department_ids": department_ids,
                "can_see_all_tickets_from_associated_departments": can_see_all_tickets_from_associated_departments,
                "reporting_manager_id": reporting_manager_id,
                "address": address,
                "time_zone": time_zone,
                "time_format": time_format,
                "language": language,
                "location_id": location_id,
                "background_information": background_information,
                "scoreboard_level_id": scoreboard_level_id,
                "roles": roles,
                "signature": signature,
                "custom_fields": custom_fields,
                "workspace_ids": workspace_ids
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
                "page": page
            }
            # Remove None values
            search_params = {k: v for k, v in search_params.items() if v is not None}

            response = self._run_async(self.client.filter_tickets(**search_params))
            return self._handle_response(response, "Ticket search completed successfully")
        except Exception as e:
            logger.error(f"Error searching tickets: {e}")
            return False, json.dumps({"error": str(e)})
