#!/usr/bin/env python3
"""
Freshservice Product FreshDesk API Code Generator

Generates a `FreshdeskDataSource` class from FreshDesk API documentation.
Since FreshDesk doesn't provide OpenAPI/Swagger specs, we manually define
the API endpoints based on official documentation.

This generator creates async wrapper methods for the FreshDesk REST API.
"""

import argparse
import keyword
import logging
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Set up logger
logger = logging.getLogger(__name__)

DEFAULT_OUT = "freshdesk.py"
DEFAULT_CLASS = "FreshdeskDataSource"


@dataclass
class Parameter:
    """Represents an API parameter"""
    name: str
    type: str
    required: bool
    description: str
    default: Optional[Any] = None


@dataclass
class Endpoint:
    """Represents a FreshDesk API endpoint"""
    name: str
    method: str
    path: str
    description: str
    parameters: List[Parameter]
    returns: str
    namespace: str
    example: Optional[str] = None


class FreshdeskAPIDefinition:
    """Define FreshDesk API endpoints based on official documentation"""

    @staticmethod
    def get_ticket_endpoints() -> List[Endpoint]:
        """Define all ticket-related endpoints"""
        return [
            # Create a Ticket
            Endpoint(
                name="create_ticket",
                method="POST",
                path="/api/v2/tickets",
                description="Create a new ticket in FreshDesk",
                namespace="tickets",
                parameters=[
                    Parameter("subject", "str", True, "Subject of the ticket"),
                    Parameter("description", "str", False, "HTML content of the ticket"),
                    Parameter("email", "str", False, "Email address of the requester"),
                    Parameter("requester_id", "int", False, "User ID of the requester"),
                    Parameter("phone", "str", False, "Phone number of the requester"),
                    Parameter("priority", "int", False, "Priority of the ticket (1-4)", 1),
                    Parameter("status", "int", False, "Status of the ticket (2-5)", 2),
                    Parameter("source", "int", False, "Source of the ticket", 2),
                    Parameter("tags", "List[str]", False, "Tags associated with the ticket"),
                    Parameter("cc_emails", "List[str]", False, "CC email addresses"),
                    Parameter("custom_fields", "Dict[str, Any]", False, "Custom field values"),
                    Parameter("attachments", "List[str]", False, "File paths for attachments"),
                ],
                returns="Ticket",
                example='ticket = await ds.create_ticket(subject="Issue", email="user@example.com")'
            ),

            # Create an Outbound Email
            Endpoint(
                name="create_outbound_email",
                method="POST",
                path="/api/v2/tickets/outbound_email",
                description="Create an outbound email ticket",
                namespace="tickets",
                parameters=[
                    Parameter("subject", "str", True, "Subject of the ticket"),
                    Parameter("email", "str", True, "Email address of the recipient"),
                    Parameter("description", "str", False, "HTML content of the ticket"),
                    Parameter("priority", "int", False, "Priority of the ticket (1-4)", 1),
                    Parameter("status", "int", False, "Status of the ticket (2-5)", 5),
                    Parameter("custom_fields", "Dict[str, Any]", False, "Custom field values"),
                ],
                returns="Ticket",
                example='ticket = await ds.create_outbound_email(subject="Info", email="user@example.com")'
            ),

            # View a Ticket
            Endpoint(
                name="get_ticket",
                method="GET",
                path="/api/v2/tickets/[id]",
                description="Retrieve a specific ticket by ID",
                namespace="tickets",
                parameters=[
                    Parameter("id", "int", True, "ID of the ticket to retrieve"),
                    Parameter("include", "str", False, "Embed additional details (conversations, requester, company, stats)"),
                ],
                returns="Ticket",
                example='ticket = await ds.get_ticket(id=123)'
            ),

            # List All Tickets
            Endpoint(
                name="list_tickets",
                method="GET",
                path="/api/v2/tickets",
                description="List all tickets with optional filters",
                namespace="tickets",
                parameters=[
                    Parameter("filter_name", "str", False, "Predefined filter (new_and_my_open, watching, spam, deleted)"),
                    Parameter("updated_since", "str", False, "Filter tickets updated after this timestamp (ISO format)"),
                    Parameter("page", "int", False, "Page number for pagination", 1),
                    Parameter("per_page", "int", False, "Number of tickets per page (max 100)", 30),
                    Parameter("include", "str", False, "Embed additional details"),
                ],
                returns="List[Ticket]",
                example='tickets = await ds.list_tickets(filter_name="new_and_my_open", per_page=10)'
            ),

            # Filter Tickets
            Endpoint(
                name="filter_tickets",
                method="GET",
                path="/api/v2/search/tickets",
                description="Filter tickets using custom query",
                namespace="tickets",
                parameters=[
                    Parameter("query", "str", True, "Filter query string (e.g., 'priority:3')"),
                    Parameter("page", "int", False, "Page number for pagination", 1),
                ],
                returns="Dict[str, Any]",
                example='result = await ds.filter_tickets(query="priority:3 AND status:2")'
            ),

            # Update a Ticket
            Endpoint(
                name="update_ticket",
                method="PUT",
                path="/api/v2/tickets/[id]",
                description="Update an existing ticket",
                namespace="tickets",
                parameters=[
                    Parameter("id", "int", True, "ID of the ticket to update"),
                    Parameter("subject", "str", False, "New subject for the ticket"),
                    Parameter("description", "str", False, "New description"),
                    Parameter("priority", "int", False, "New priority (1-4)"),
                    Parameter("status", "int", False, "New status (2-5)"),
                    Parameter("tags", "List[str]", False, "Tags to associate"),
                    Parameter("custom_fields", "Dict[str, Any]", False, "Custom field values"),
                ],
                returns="Ticket",
                example='ticket = await ds.update_ticket(ticket_id=123, priority=4, status=3)'
            ),

            # Delete a Ticket
            Endpoint(
                name="delete_ticket",
                method="DELETE",
                path="/api/v2/tickets/[id]",
                description="Delete a ticket (moves to trash)",
                namespace="tickets",
                parameters=[
                    Parameter("id", "int", True, "ID of the ticket to delete"),
                ],
                returns="None",
                example='await ds.delete_ticket(ticket_id=123)'
            ),

            # List All Ticket Fields
            Endpoint(
                name="list_ticket_fields",
                method="GET",
                path="/api/v2/ticket_fields",
                description="List all ticket fields including custom fields",
                namespace="ticket_fields",
                parameters=[
                    Parameter("type", "str", False, "Filter by field type"),
                ],
                returns="List[TicketField]",
                example='fields = await ds.list_ticket_fields()'
            ),

            # List All Conversations
            Endpoint(
                name="list_ticket_conversations",
                method="GET",
                path="/api/v2/tickets/[id]/conversations",
                description="List all conversations (notes/replies) for a ticket",
                namespace="comments",
                parameters=[
                    Parameter("id", "int", True, "ID of the ticket"),
                    Parameter("page", "int", False, "Page number for pagination", 1),
                    Parameter("per_page", "int", False, "Number of conversations per page", 30),
                ],
                returns="List[Comment]",
                example='conversations = await ds.list_ticket_conversations(ticket_id=123)'
            ),

            # Create a Note
            Endpoint(
                name="create_note",
                method="POST",
                path="/api/v2/tickets/[id]/notes",
                description="Add a note to a ticket",
                namespace="comments",
                parameters=[
                    Parameter("id", "int", True, "ID of the ticket"),
                    Parameter("body", "str", True, "Content of the note"),
                    Parameter("private", "bool", False, "Whether the note is private", True),
                    Parameter("notify_emails", "List[str]", False, "Email addresses to notify"),
                ],
                returns="Comment",
                example='note = await ds.create_note(ticket_id=123, body="Internal note", private=True)'
            ),

            # Create a Reply
            Endpoint(
                name="create_reply",
                method="POST",
                path="/api/v2/tickets/[id]/reply",
                description="Reply to a ticket",
                namespace="comments",
                parameters=[
                    Parameter("id", "int", True, "ID of the ticket"),
                    Parameter("body", "str", True, "Content of the reply"),
                    Parameter("cc_emails", "List[str]", False, "CC email addresses"),
                    Parameter("bcc_emails", "List[str]", False, "BCC email addresses"),
                ],
                returns="Comment",
                example='reply = await ds.create_reply(ticket_id=123, body="Thank you for reporting")'
            ),

            # List Deleted Tickets
            Endpoint(
                name="list_deleted_tickets",
                method="GET",
                path="/api/v2/tickets",
                description="List all deleted tickets",
                namespace="tickets",
                parameters=[
                    Parameter("page", "int", False, "Page number for pagination", 1),
                    Parameter("per_page", "int", False, "Number of tickets per page", 30),
                ],
                returns="List[Ticket]",
                example='deleted = await ds.list_deleted_tickets()'
            ),

            # Restore a Ticket
            Endpoint(
                name="restore_ticket",
                method="PUT",
                path="/api/v2/tickets/[id]/restore",
                description="Restore a deleted ticket",
                namespace="tickets",
                parameters=[
                    Parameter("id", "int", True, "ID of the ticket to restore"),
                ],
                returns="None",
                example='await ds.restore_ticket(ticket_id=123)'
            ),
        ]

    @staticmethod
    def get_problem_endpoints() -> List[Endpoint]:
        """Define all problem-related endpoints"""
        return [
            # Create a Problem
            Endpoint(
                name="create_problem",
                method="POST",
                path="/api/v2/problems",
                description="Create a new problem in FreshDesk",
                namespace="problems",
                parameters=[
                    Parameter("subject", "str", True, "Subject of the problem"),
                    Parameter("description", "str", False, "HTML content of the problem"),
                    Parameter("requester_id", "int", False, "User ID of the requester"),
                    Parameter("priority", "int", False, "Priority of the problem (1-4)", 1),
                    Parameter("status", "int", False, "Status of the problem (1-3)", 1),
                    Parameter("impact", "int", False, "Impact of the problem (1-3)", 1),
                    Parameter("group_id", "int", False, "ID of the group to assign"),
                    Parameter("agent_id", "int", False, "ID of the agent to assign"),
                    Parameter("department_id", "int", False, "ID of the department"),
                    Parameter("category", "str", False, "Category of the problem"),
                    Parameter("sub_category", "str", False, "Sub-category of the problem"),
                    Parameter("item_category", "str", False, "Item category"),
                    Parameter("due_by", "str", False, "Due date (ISO format)"),
                    Parameter("known_error", "bool", False, "Whether this is a known error", False),
                    Parameter("custom_fields", "Dict[str, Any]", False, "Custom field values"),
                    Parameter("assets", "List[Dict[str, int]]", False, "Associated assets"),
                ],
                returns="Problem",
                example='problem = await ds.create_problem(subject="Root cause", requester_id=123)'
            ),

            # View a Problem
            Endpoint(
                name="get_problem",
                method="GET",
                path="/api/v2/problems/[id]",
                description="Retrieve a specific problem by ID",
                namespace="problems",
                parameters=[
                    Parameter("id", "int", True, "ID of the problem to retrieve"),
                ],
                returns="Problem",
                example='problem = await ds.get_problem(id=456)'
            ),

            # List All Problems
            Endpoint(
                name="list_problems",
                method="GET",
                path="/api/v2/problems",
                description="List all problems with optional filters",
                namespace="problems",
                parameters=[
                    Parameter("predefined_filter", "str", False, "Predefined filter name"),
                    Parameter("requester_id", "int", False, "Filter by requester ID"),
                    Parameter("page", "int", False, "Page number for pagination", 1),
                    Parameter("per_page", "int", False, "Number of problems per page (max 100)", 30),
                ],
                returns="List[Problem]",
                example='problems = await ds.list_problems(per_page=10)'
            ),

            # Update a Problem
            Endpoint(
                name="update_problem",
                method="PUT",
                path="/api/v2/problems/[id]",
                description="Update an existing problem",
                namespace="problems",
                parameters=[
                    Parameter("id", "int", True, "ID of the problem to update"),
                    Parameter("subject", "str", False, "New subject"),
                    Parameter("description", "str", False, "New description"),
                    Parameter("priority", "int", False, "New priority (1-4)"),
                    Parameter("status", "int", False, "New status (1-3)"),
                    Parameter("impact", "int", False, "New impact (1-3)"),
                    Parameter("known_error", "bool", False, "Mark as known error"),
                    Parameter("group_id", "int", False, "Reassign to group"),
                    Parameter("agent_id", "int", False, "Reassign to agent"),
                    Parameter("custom_fields", "Dict[str, Any]", False, "Custom field values"),
                ],
                returns="Problem",
                example='problem = await ds.update_problem(id=456, status=2, priority=3)'
            ),

            # Delete a Problem
            Endpoint(
                name="delete_problem",
                method="DELETE",
                path="/api/v2/problems/[id]",
                description="Delete a problem (moves to trash)",
                namespace="problems",
                parameters=[
                    Parameter("id", "int", True, "ID of the problem to delete"),
                ],
                returns="None",
                example='await ds.delete_problem(id=456)'
            ),

            # Restore a Problem
            Endpoint(
                name="restore_problem",
                method="PUT",
                path="/api/v2/problems/[id]/restore",
                description="Restore a deleted problem",
                namespace="problems",
                parameters=[
                    Parameter("id", "int", True, "ID of the problem to restore"),
                ],
                returns="None",
                example='await ds.restore_problem(id=456)'
            ),

            # List Deleted Problems
            Endpoint(
                name="list_deleted_problems",
                method="GET",
                path="/api/v2/problems/deleted",
                description="List all deleted problems",
                namespace="problems",
                parameters=[
                    Parameter("page", "int", False, "Page number for pagination", 1),
                    Parameter("per_page", "int", False, "Number per page", 30),
                ],
                returns="List[Problem]",
                example='deleted = await ds.list_deleted_problems()'
            ),

            # Create Problem Note
            Endpoint(
                name="create_problem_note",
                method="POST",
                path="/api/v2/problems/[id]/notes",
                description="Add a note to a problem",
                namespace="problems",
                parameters=[
                    Parameter("id", "int", True, "ID of the problem"),
                    Parameter("body", "str", True, "Content of the note"),
                    Parameter("notify_emails", "List[str]", False, "Emails to notify"),
                ],
                returns="Note",
                example='note = await ds.create_problem_note(id=456, body="Root cause identified")'
            ),

            # List Problem Tasks
            Endpoint(
                name="list_problem_tasks",
                method="GET",
                path="/api/v2/problems/[id]/tasks",
                description="List all tasks associated with a problem",
                namespace="problems",
                parameters=[
                    Parameter("id", "int", True, "ID of the problem"),
                    Parameter("page", "int", False, "Page number", 1),
                    Parameter("per_page", "int", False, "Number per page", 30),
                ],
                returns="List[Task]",
                example='tasks = await ds.list_problem_tasks(id=456)'
            ),

            # Create Problem Task
            Endpoint(
                name="create_problem_task",
                method="POST",
                path="/api/v2/problems/[id]/tasks",
                description="Create a task for a problem",
                namespace="problems",
                parameters=[
                    Parameter("id", "int", True, "ID of the problem"),
                    Parameter("title", "str", True, "Title of the task"),
                    Parameter("description", "str", False, "Description of the task"),
                    Parameter("status", "int", False, "Status of the task (1-3)", 1),
                    Parameter("due_date", "str", False, "Due date (ISO format)"),
                    Parameter("notify_before", "int", False, "Notify before due (in hours)"),
                    Parameter("agent_id", "int", False, "Agent assigned to task"),
                    Parameter("group_id", "int", False, "Group assigned to task"),
                ],
                returns="Task",
                example='task = await ds.create_problem_task(id=456, title="Investigate logs")'
            ),

            # Update Problem Task
            Endpoint(
                name="update_problem_task",
                method="PUT",
                path="/api/v2/problems/[problem_id]/tasks/[task_id]",
                description="Update a problem task",
                namespace="problems",
                parameters=[
                    Parameter("problem_id", "int", True, "ID of the problem"),
                    Parameter("task_id", "int", True, "ID of the task"),
                    Parameter("title", "str", False, "New title"),
                    Parameter("description", "str", False, "New description"),
                    Parameter("status", "int", False, "New status (1-3)"),
                    Parameter("agent_id", "int", False, "Reassign to agent"),
                ],
                returns="Task",
                example='task = await ds.update_problem_task(problem_id=456, task_id=1, status=2)'
            ),

            # Delete Problem Task
            Endpoint(
                name="delete_problem_task",
                method="DELETE",
                path="/api/v2/problems/[problem_id]/tasks/[task_id]",
                description="Delete a problem task",
                namespace="problems",
                parameters=[
                    Parameter("problem_id", "int", True, "ID of the problem"),
                    Parameter("task_id", "int", True, "ID of the task"),
                ],
                returns="None",
                example='await ds.delete_problem_task(problem_id=456, task_id=1)'
            ),

            # List Problem Time Entries
            Endpoint(
                name="list_problem_time_entries",
                method="GET",
                path="/api/v2/problems/[id]/time_entries",
                description="List all time entries for a problem",
                namespace="problems",
                parameters=[
                    Parameter("id", "int", True, "ID of the problem"),
                ],
                returns="List[TimeEntry]",
                example='entries = await ds.list_problem_time_entries(id=456)'
            ),
        ]

    @staticmethod
    def get_agent_endpoints() -> List[Endpoint]:
        """Define all agent-related endpoints"""
        return [
            # Create Agent
            Endpoint(
                name="create_agent",
                method="POST",
                path="/api/v2/agents",
                description="Create a new agent in FreshDesk",
                namespace="agents",
                parameters=[
                    Parameter("first_name", "str", True, "First name of the agent"),
                    Parameter("email", "str", True, "Email address of the agent"),
                    Parameter("last_name", "str", False, "Last name of the agent"),
                    Parameter("occasional", "bool", False, "True if occasional agent, false if full-time", False),
                    Parameter("job_title", "str", False, "Job title of the agent"),
                    Parameter("work_phone_number", "str", False, "Work phone number"),
                    Parameter("mobile_phone_number", "str", False, "Mobile phone number"),
                    Parameter("department_ids", "List[int]", False, "IDs of departments"),
                    Parameter("can_see_all_tickets_from_associated_departments", "bool", False, "Can view all department tickets", False),
                    Parameter("reporting_manager_id", "int", False, "User ID of reporting manager"),
                    Parameter("address", "str", False, "Address of the agent"),
                    Parameter("time_zone", "str", False, "Time zone"),
                    Parameter("time_format", "str", False, "Time format (12h or 24h)"),
                    Parameter("language", "str", False, "Language code"),
                    Parameter("location_id", "int", False, "Location ID"),
                    Parameter("background_information", "str", False, "Background information"),
                    Parameter("scoreboard_level_id", "int", False, "Scoreboard level ID"),
                    Parameter("roles", "List[Dict[str, Any]]", False, "Array of role objects"),
                    Parameter("signature", "str", False, "Signature in HTML format"),
                    Parameter("custom_fields", "Dict[str, Any]", False, "Custom field values"),
                    Parameter("workspace_ids", "List[int]", False, "Workspace IDs"),
                ],
                returns="Agent",
                example='agent = await ds.create_agent(first_name="John", email="john@example.com")'
            ),

            # View Agent
            Endpoint(
                name="view_agent",
                method="GET",
                path="/api/v2/agents/[id]",
                description="View information about a specific agent",
                namespace="agents",
                parameters=[
                    Parameter("id", "int", True, "ID of the agent"),
                ],
                returns="Agent",
                example='agent = await ds.view_agent(id=123)'
            ),

            # List All Agents
            Endpoint(
                name="list_agents",
                method="GET",
                path="/api/v2/agents",
                description="List all agents in the account",
                namespace="agents",
                parameters=[
                    Parameter("email", "str", False, "Filter by email"),
                    Parameter("mobile_phone_number", "str", False, "Filter by mobile phone"),
                    Parameter("work_phone_number", "str", False, "Filter by work phone"),
                    Parameter("active", "bool", False, "Filter by active status"),
                    Parameter("state", "str", False, "Filter by state (fulltime/occasional)"),
                    Parameter("page", "int", False, "Page number", 1),
                    Parameter("per_page", "int", False, "Number of entries per page", 30),
                ],
                returns="List[Agent]",
                example='agents = await ds.list_agents(active=True)'
            ),

            # Filter Agents
            Endpoint(
                name="filter_agents",
                method="GET",
                path="/api/v2/agents?query=[query]",
                description="Filter agents using query string",
                namespace="agents",
                parameters=[
                    Parameter("query", "str", True, "Query string for filtering agents"),
                    Parameter("page", "int", False, "Page number", 1),
                    Parameter("per_page", "int", False, "Number of entries per page", 30),
                ],
                returns="List[Agent]",
                example='agents = await ds.filter_agents(query="email:\'john@example.com\'")'
            ),

            # Update Agent
            Endpoint(
                name="update_agent",
                method="PUT",
                path="/api/v2/agents/[id]",
                description="Update an existing agent",
                namespace="agents",
                parameters=[
                    Parameter("id", "int", True, "ID of the agent"),
                    Parameter("first_name", "str", False, "First name of the agent"),
                    Parameter("last_name", "str", False, "Last name of the agent"),
                    Parameter("occasional", "bool", False, "True if occasional agent"),
                    Parameter("job_title", "str", False, "Job title"),
                    Parameter("email", "str", False, "Email address"),
                    Parameter("work_phone_number", "str", False, "Work phone number"),
                    Parameter("mobile_phone_number", "str", False, "Mobile phone number"),
                    Parameter("department_ids", "List[int]", False, "Department IDs"),
                    Parameter("can_see_all_tickets_from_associated_departments", "bool", False, "Can view all department tickets"),
                    Parameter("reporting_manager_id", "int", False, "Reporting manager ID"),
                    Parameter("address", "str", False, "Address"),
                    Parameter("time_zone", "str", False, "Time zone"),
                    Parameter("time_format", "str", False, "Time format"),
                    Parameter("language", "str", False, "Language code"),
                    Parameter("location_id", "int", False, "Location ID"),
                    Parameter("background_information", "str", False, "Background information"),
                    Parameter("scoreboard_level_id", "int", False, "Scoreboard level ID"),
                    Parameter("roles", "List[Dict[str, Any]]", False, "Array of role objects"),
                    Parameter("signature", "str", False, "Signature"),
                    Parameter("custom_fields", "Dict[str, Any]", False, "Custom fields"),
                ],
                returns="Agent",
                example='agent = await ds.update_agent(id=123, job_title="Senior Engineer")'
            ),

            # Deactivate Agent
            Endpoint(
                name="deactivate_agent",
                method="DELETE",
                path="/api/v2/agents/[id]",
                description="Deactivate an agent",
                namespace="agents",
                parameters=[
                    Parameter("id", "int", True, "ID of the agent"),
                ],
                returns="None",
                example='await ds.deactivate_agent(id=123)'
            ),

            # Forget Agent
            Endpoint(
                name="forget_agent",
                method="DELETE",
                path="/api/v2/agents/[id]/forget",
                description="Permanently delete an agent and their tickets",
                namespace="agents",
                parameters=[
                    Parameter("id", "int", True, "ID of the agent"),
                ],
                returns="None",
                example='await ds.forget_agent(id=123)'
            ),

            # Reactivate Agent
            Endpoint(
                name="reactivate_agent",
                method="PUT",
                path="/api/v2/agents/[id]/reactivate",
                description="Reactivate a deactivated agent",
                namespace="agents",
                parameters=[
                    Parameter("id", "int", True, "ID of the agent"),
                ],
                returns="Agent",
                example='agent = await ds.reactivate_agent(id=123)'
            ),

            # List Agent Fields
            Endpoint(
                name="list_agent_fields",
                method="GET",
                path="/api/v2/agent_fields",
                description="List all built-in and custom fields for agents",
                namespace="agents",
                parameters=[
                    Parameter("include", "str", False, "Include additional details (e.g., 'user_field_groups')"),
                ],
                returns="List[AgentField]",
                example='fields = await ds.list_agent_fields()'
            ),
        ]

    @staticmethod
    def get_software_endpoints() -> List[Endpoint]:
        """Define all software/application-related endpoints"""
        return [
            # Create Software
            Endpoint(
                name="create_software",
                method="POST",
                path="/api/v2/applications",
                description="Create a new software/application in FreshDesk",
                namespace="software",
                parameters=[
                    Parameter("name", "str", True, "Name of the software"),
                    Parameter("description", "str", False, "Description of the software"),
                    Parameter("application_type", "str", False, "Type of application (desktop/saas/mobile)", "desktop"),
                    Parameter("status", "str", False, "Status (blacklisted/ignored/managed)"),
                    Parameter("publisher_id", "int", False, "ID of the Vendor/Publisher"),
                    Parameter("managed_by_id", "int", False, "ID of the user managing the software"),
                    Parameter("notes", "str", False, "Notes about the software"),
                    Parameter("category", "str", False, "Category of the software"),
                    Parameter("source", "str", False, "Source of software details (API, Okta, Google, etc.)"),
                    Parameter("workspace_id", "int", False, "Workspace ID"),
                ],
                returns="Software",
                example='software = await ds.create_software(name="FreshDesk", application_type="saas")'
            ),

            # View Software
            Endpoint(
                name="view_software",
                method="GET",
                path="/api/v2/applications/[id]",
                description="View a specific software/application by ID",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software"),
                ],
                returns="Software",
                example='software = await ds.view_software(id=123)'
            ),

            # List All Software
            Endpoint(
                name="list_software",
                method="GET",
                path="/api/v2/applications",
                description="List all software/applications",
                namespace="software",
                parameters=[
                    Parameter("workspace_id", "int", False, "Workspace ID (0 for all workspaces)"),
                    Parameter("page", "int", False, "Page number", 1),
                    Parameter("per_page", "int", False, "Number of entries per page", 30),
                ],
                returns="List[Software]",
                example='software_list = await ds.list_software()'
            ),

            # Update Software
            Endpoint(
                name="update_software",
                method="PUT",
                path="/api/v2/applications/[id]",
                description="Update an existing software/application",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software"),
                    Parameter("name", "str", False, "Name of the software"),
                    Parameter("description", "str", False, "Description of the software"),
                    Parameter("application_type", "str", False, "Type of application (desktop/saas/mobile)"),
                    Parameter("status", "str", False, "Status (blacklisted/ignored/managed)"),
                    Parameter("publisher_id", "int", False, "ID of the Vendor/Publisher"),
                    Parameter("managed_by_id", "int", False, "ID of the user managing the software"),
                    Parameter("notes", "str", False, "Notes about the software"),
                    Parameter("category", "str", False, "Category of the software"),
                    Parameter("source", "str", False, "Source of software details"),
                ],
                returns="Software",
                example='software = await ds.update_software(id=123, status="managed")'
            ),

            # Delete Software
            Endpoint(
                name="delete_software",
                method="DELETE",
                path="/api/v2/applications/[id]",
                description="Delete a specific software/application",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software to delete"),
                ],
                returns="None",
                example='await ds.delete_software(id=123)'
            ),

            # List Software Licenses
            Endpoint(
                name="list_software_licenses",
                method="GET",
                path="/api/v2/applications/[id]/licenses",
                description="List all licenses of a software",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software"),
                ],
                returns="List[SoftwareLicense]",
                example='licenses = await ds.list_software_licenses(id=123)'
            ),

            # Add Users to Software (Bulk)
            Endpoint(
                name="add_software_users",
                method="POST",
                path="/api/v2/applications/[id]/users",
                description="Add users to a software in bulk",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software"),
                    Parameter("application_users", "List[Dict[str, Any]]", True, "List of application user objects"),
                ],
                returns="List[SoftwareUser]",
                example='users = await ds.add_software_users(id=123, application_users=[{"user_id": 456}])'
            ),

            # View Software User
            Endpoint(
                name="view_software_user",
                method="GET",
                path="/api/v2/applications/[id]/users/[user_id]",
                description="View a specific user of a software",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software"),
                    Parameter("user_id", "int", True, "ID of the application user"),
                ],
                returns="SoftwareUser",
                example='user = await ds.view_software_user(id=123, user_id=456)'
            ),

            # List Software Users
            Endpoint(
                name="list_software_users",
                method="GET",
                path="/api/v2/applications/[id]/users",
                description="List all users of a software",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software"),
                    Parameter("page", "int", False, "Page number", 1),
                    Parameter("per_page", "int", False, "Number of entries per page", 30),
                ],
                returns="List[SoftwareUser]",
                example='users = await ds.list_software_users(id=123)'
            ),

            # Update Software Users (Bulk)
            Endpoint(
                name="update_software_users",
                method="PUT",
                path="/api/v2/applications/[id]/users",
                description="Update users of a software in bulk",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software"),
                    Parameter("application_users", "List[Dict[str, Any]]", True, "List of application user objects to update"),
                ],
                returns="List[SoftwareUser]",
                example='users = await ds.update_software_users(id=123, application_users=[{"user_id": 456, "license_id": 10}])'
            ),

            # Remove Software Users (Bulk)
            Endpoint(
                name="remove_software_users",
                method="DELETE",
                path="/api/v2/applications/[id]/users",
                description="Remove users from a software in bulk",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software"),
                    Parameter("user_ids", "List[int]", True, "List of user IDs to remove"),
                ],
                returns="None",
                example='await ds.remove_software_users(id=123, user_ids=[456, 789])'
            ),

            # Add Installation to Software
            Endpoint(
                name="add_software_installation",
                method="POST",
                path="/api/v2/applications/[id]/installations",
                description="Add a device installation to a software",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software"),
                    Parameter("installation_machine_id", "int", True, "Display ID of device"),
                    Parameter("installation_path", "str", False, "Path where software is installed"),
                    Parameter("version", "str", False, "Version of installed software"),
                    Parameter("installation_date", "str", False, "Installation date (ISO format)"),
                ],
                returns="SoftwareInstallation",
                example='installation = await ds.add_software_installation(id=123, installation_machine_id=456)'
            ),

            # List Software Installations
            Endpoint(
                name="list_software_installations",
                method="GET",
                path="/api/v2/applications/[id]/installations",
                description="List all installations of a software",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software"),
                    Parameter("page", "int", False, "Page number", 1),
                    Parameter("per_page", "int", False, "Number of entries per page", 30),
                ],
                returns="List[SoftwareInstallation]",
                example='installations = await ds.list_software_installations(id=123)'
            ),

            # Remove Software Installations (Bulk)
            Endpoint(
                name="remove_software_installations",
                method="DELETE",
                path="/api/v2/applications/[id]/installations",
                description="Remove device installations from a software in bulk",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software"),
                    Parameter("device_ids", "List[int]", True, "List of device display IDs to remove"),
                ],
                returns="None",
                example='await ds.remove_software_installations(id=123, device_ids=[456, 789])'
            ),

            # Move Software to Workspace
            Endpoint(
                name="move_software",
                method="PUT",
                path="/api/v2/applications/[id]/move_workspace",
                description="Move software to a different workspace",
                namespace="software",
                parameters=[
                    Parameter("id", "int", True, "ID of the software"),
                    Parameter("workspace_id", "int", True, "ID of the target workspace"),
                ],
                returns="Software",
                example='software = await ds.move_software(id=123, workspace_id=2)'
            ),
        ]


class FreshdeskCodeGenerator:
    """Generate FreshDesk DataSource class"""

    def __init__(self) -> None:
        self.generated_methods: List[Dict[str, Any]] = []

    @staticmethod
    def sanitize_py_name(name: str) -> str:
        """Sanitize parameter names to be valid Python identifiers"""
        n = re.sub(r'[^0-9a-zA-Z_]', '_', name)
        if n and n[0].isdigit():
            n = f"_{n}"
        if keyword.iskeyword(n):
            n += "_"
        return n

    def build_method_signature(self, endpoint: Endpoint) -> Tuple[str, List[str]]:
        """Build method signature from endpoint definition"""
        required_params = []
        optional_params = []

        for param in endpoint.parameters:
            py_name = self.sanitize_py_name(param.name)

            if param.required:
                required_params.append(f"{py_name}: {param.type}")
            else:
                if param.default is not None:
                    if isinstance(param.default, str):
                        default_val = f'"{param.default}"'
                    else:
                        default_val = str(param.default)
                else:
                    default_val = "None"
                optional_params.append(f"{py_name}: Optional[{param.type}] = {default_val}")

        all_params = ['self'] + required_params + optional_params

        if len(all_params) == 1:
            signature = f"async def {endpoint.name}(self) -> FreshDeskResponse:"
        else:
            params_formatted = ',\n        '.join(all_params)
            signature = f"async def {endpoint.name}(\n        {params_formatted}\n    ) -> FreshDeskResponse:"

        return signature, all_params[1:]  # Return params without 'self'

    def build_docstring(self, endpoint: Endpoint) -> str:
        """Build method docstring"""
        docstring = f'        """{endpoint.description}\n\n'
        docstring += f'        API Endpoint: {endpoint.method} {endpoint.path}\n'

        if endpoint.parameters:
            docstring += '\n        Args:\n'
            for param in endpoint.parameters:
                py_name = self.sanitize_py_name(param.name)
                required_text = 'required' if param.required else 'optional'
                docstring += f'            {py_name} ({param.type}, {required_text}): {param.description}\n'

        docstring += '\n        Returns:\n            FreshDeskResponse: Standardized response wrapper\n'

        if endpoint.example:
            docstring += f'\n        Example:\n            {endpoint.example}\n'

        docstring += '        """'
        return docstring

    def build_method_body(self, endpoint: Endpoint, params: List[str]) -> str:
        """Build method implementation using HTTP requests"""

        # Determine HTTP method and build URL
        http_method = endpoint.method.upper()

        # Build request body or params
        body_params = []
        url_params = []
        path_params = []

        for param in endpoint.parameters:
            py_name = self.sanitize_py_name(param.name)
            # Check if parameter is in path
            if f"[{param.name}]" in endpoint.path or f"{{{param.name}}}" in endpoint.path:
                path_params.append((param.name, py_name))
            elif http_method in ["GET", "DELETE"]:
                url_params.append((param.name, py_name))
            else:
                body_params.append((param.name, py_name))

        # Build URL construction
        url_parts = []
        url_parts.append("        url = self._freshdesk_client.get_base_url()")

        # Replace path parameters and strip /api/v2 since it's already in base_url
        path = endpoint.path
        if path.startswith("/api/v2"):
            path = path[7:]  # Remove "/api/v2" prefix

        for param_name, py_name in path_params:
            path = path.replace(f"[{param_name}]", f"{{{py_name}}}")
            path = path.replace(f"{{{param_name}}}", f"{{{py_name}}}")

        url_parts.append(f"        url += f\"{path}\"")

        # Build query parameters for GET requests
        if url_params and http_method == "GET":
            url_parts.append("        params = {}")
            for param_name, py_name in url_params:
                url_parts.append(f"        if {py_name} is not None:")
                url_parts.append(f"            params['{param_name}'] = {py_name}")
            url_parts.append("        if params:")
            url_parts.append("            from urllib.parse import urlencode")
            url_parts.append("            url += '?' + urlencode(params)")

        url_code = '\n'.join(url_parts)

        # Build request body for POST/PUT
        body_code = ""
        if body_params and http_method in ["POST", "PUT"]:
            body_code = """
        request_body: Dict[str, Any] = {}"""
            for param_name, py_name in body_params:
                body_code += f"""
        if {py_name} is not None:
            request_body['{param_name}'] = {py_name}"""
        else:
            body_code = "\n        request_body = None"

        # Build complete method body
        body = f"""{url_code}{body_code}

        try:
            request = HTTPRequest(
                url=url,
                method="{http_method}",
                headers={{"Content-Type": "application/json"}},
                body=request_body
            )
            response: HTTPResponse = await self.http_client.execute(request)

            # Debug logging
            response_text = response.text()
            if response.status >= HTTP_ERROR_THRESHOLD:
                logger.debug(f"{endpoint.name}: Status={{response.status}}, Response={{response_text[:200] if response_text else 'Empty'}}")

            return FreshDeskResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response_text else None,
                message=f"Successfully executed {endpoint.name}" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {{response.status}}"
            )
        except Exception as e:
            logger.debug(f"Error in {endpoint.name}: {{e}}")
            return FreshDeskResponse(
                success=False,
                error=str(e),
                message=f"Failed to execute {endpoint.name}"
            )"""

        return body

    def generate_method(self, endpoint: Endpoint) -> str:
        """Generate complete method code"""
        signature, params = self.build_method_signature(endpoint)
        docstring = self.build_docstring(endpoint)
        body = self.build_method_body(endpoint, params)

        self.generated_methods.append({
            'name': endpoint.name,
            'namespace': endpoint.namespace,
            'method': endpoint.method,
            'params': len(endpoint.parameters),
            'path': endpoint.path
        })

        return f"    {signature}\n{docstring}\n{body}\n\n"

    def generate_class(self, class_name: str, endpoints: List[Endpoint]) -> str:
        """Generate complete DataSource class"""

        header = f'''"""
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


class {class_name}:
    """FreshDesk API DataSource

    Provides async wrapper methods for FreshDesk API operations.
    All methods return standardized FreshDeskResponse objects.

    Generated methods: {len(endpoints)}
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

'''

        # Generate all methods
        methods_code = ""
        for endpoint in endpoints:
            methods_code += self.generate_method(endpoint)

        return header + methods_code

def generate_freshdesk_client(
    *,
    out_path: str = DEFAULT_OUT,
    class_name: str = DEFAULT_CLASS,
) -> str:
    """Generate the FreshDesk DataSource Python file"""

    print("Generating FreshDesk DataSource...")


    # Get all endpoints
    endpoints = FreshdeskAPIDefinition.get_ticket_endpoints()
    endpoints.extend(FreshdeskAPIDefinition.get_problem_endpoints())
    endpoints.extend(FreshdeskAPIDefinition.get_agent_endpoints())
    endpoints.extend(FreshdeskAPIDefinition.get_software_endpoints())

    # Generate code
    generator = FreshdeskCodeGenerator()
    code = generator.generate_class(class_name, endpoints)

    # Write file in freshdesk subdirectory of the generator script directory
    script_dir = Path(__file__).parent if __file__ else Path('.')
    freshdesk_dir = script_dir / 'freshdesk'
    freshdesk_dir.mkdir(parents=True, exist_ok=True)

    full_path = freshdesk_dir / out_path
    full_path.write_text(code, encoding='utf-8')

    return str(full_path)


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Generate FreshDesk DataSource class"
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help="Output .py file path (default: freshdesk.py)"
    )
    parser.add_argument(
        "--class-name",
        default=DEFAULT_CLASS,
        help="Generated class name (default: FreshDeskDataSource)"
    )

    args = parser.parse_args(argv)

    try:
        out_path = generate_freshdesk_client(
            out_path=args.out,
            class_name=args.class_name
        )

        print(f"Generated {args.class_name} -> {out_path}")
        print(f"Files saved in: {Path(out_path).parent}")

        # Print summary
        ticket_endpoints = FreshdeskAPIDefinition.get_ticket_endpoints()
        problem_endpoints = FreshdeskAPIDefinition.get_problem_endpoints()
        agent_endpoints = FreshdeskAPIDefinition.get_agent_endpoints()
        software_endpoints = FreshdeskAPIDefinition.get_software_endpoints()
        all_endpoints = ticket_endpoints + problem_endpoints + agent_endpoints + software_endpoints

        print(f"\nGenerated {len(all_endpoints)} total endpoint methods:")
        print(f"  - {len(ticket_endpoints)} ticket endpoints")
        print(f"  - {len(problem_endpoints)} problem endpoints")
        print(f"  - {len(agent_endpoints)} agent endpoints")
        print(f"  - {len(software_endpoints)} software endpoints")

        namespaces: Dict[str, int] = {}
        for endpoint in all_endpoints:
            ns = endpoint.namespace
            namespaces[ns] = namespaces.get(ns, 0) + 1

        print("\nNamespaces:")
        for ns, count in sorted(namespaces.items()):
            print(f"  - {ns}: {count} methods")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
