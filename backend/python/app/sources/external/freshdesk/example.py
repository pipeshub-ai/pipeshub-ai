# ruff: noqa
import asyncio
import os
from datetime import datetime
from typing import Any, Dict, List

from app.sources.client.freshdesk.freshdesk import (
    FreshDeskApiKeyConfig,
    FreshDeskClient,
)
from app.sources.external.freshdesk.freshdesk import (
    FreshDeskDataSource,
    FreshDeskResponse,
)


def mask_sensitive_data(value: str, visible_chars: int = 3) -> str:
    """Mask sensitive data like emails and phone numbers for logging"""
    if not value or value == 'N/A':
        return value

    if '@' in value:  # Email masking
        parts = value.split('@')
        if len(parts) == 2:
            username, domain = parts
            masked_username = username[:min(visible_chars, len(username))] + '***'
            return f"{masked_username}@{domain}"

    # Phone number masking
    if len(value) > visible_chars:
        return value[:visible_chars] + '***'

    return '***'


def print_separator(char: str = "=", length: int = 80) -> None:
    """Print a separator line"""
    print(char * length)


def print_header(title: str) -> None:
    """Print a section header"""
    print_separator()
    print(f"  {title}")
    print_separator()


def print_result(title: str, response: FreshDeskResponse) -> None:
    """Print a formatted result"""
    print(f"\n{'='*80}")
    print(f"  {title}")
    print(f"{'='*80}")

    if not response.success:
        print(f"❌ Error: {response.error or response.message}")
        return

    print(f"✅ Success: {response.message}")
    if response.data:
        print(f"\n{'-'*80}")
        print("Data:")
        print(f"{'-'*80}")


def format_ticket_summary(ticket: Dict[str, Any]) -> str:
    """Format a ticket as a readable summary"""
    priority_map = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
    status_map = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}

    subject = ticket.get('subject', 'N/A')
    ticket_id = ticket.get('id', 'N/A')
    priority = priority_map.get(ticket.get('priority', 0), "Unknown")
    status = status_map.get(ticket.get('status', 0), "Unknown")
    created_at = ticket.get('created_at', 'N/A')

    # Format timestamp if available
    if created_at != 'N/A':
        try:
            dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            created_at = dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            pass

    return (
        f"  • Ticket #{ticket_id}\n"
        f"    Subject: {subject}\n"
        f"    Status: {status} | Priority: {priority}\n"
        f"    Created: {created_at}"
    )


def print_tickets_list(tickets: List[Dict[str, Any]], max_display: int = 5) -> None:
    """Print a list of tickets in a readable format"""
    if not tickets:
        print("  No tickets found.")
        return

    print(f"  Found {len(tickets)} ticket(s):\n")
    for i, ticket in enumerate(tickets[:max_display], 1):
        print(format_ticket_summary(ticket))
        if i < len(tickets[:max_display]):
            print()

    if len(tickets) > max_display:
        print(f"\n  ... and {len(tickets) - max_display} more ticket(s)")


def print_ticket_details(ticket: Dict[str, Any]) -> None:
    """Print detailed ticket information"""
    priority_map = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
    status_map = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}
    source_map = {1: "Email", 2: "Portal", 3: "Phone", 4: "Chat", 7: "Feedback Widget"}

    print(f"  Ticket ID: #{ticket.get('id', 'N/A')}")
    print(f"  Subject: {ticket.get('subject', 'N/A')}")
    print(f"  Type: {ticket.get('type', 'N/A')}")
    print(f"  Status: {status_map.get(ticket.get('status', 0), 'Unknown')}")
    print(f"  Priority: {priority_map.get(ticket.get('priority', 0), 'Unknown')}")
    print(f"  Source: {source_map.get(ticket.get('source', 0), 'Unknown')}")

    if ticket.get('description_text'):
        desc = ticket['description_text']
        print(f"  Description: {desc[:100]}{'...' if len(desc) > 100 else ''}")

    print(f"\n  Requester ID: {ticket.get('requester_id', 'N/A')}")
    print(f"  Workspace ID: {ticket.get('workspace_id', 'N/A')}")

    created = ticket.get('created_at', 'N/A')
    updated = ticket.get('updated_at', 'N/A')
    due_by = ticket.get('due_by', 'N/A')

    print(f"\n  Created: {created}")
    print(f"  Updated: {updated}")
    print(f"  Due By: {due_by}")

    if ticket.get('tags'):
        print(f"  Tags: {', '.join(ticket['tags'])}")


def format_problem_summary(problem: Dict[str, Any]) -> str:
    """Format a problem as a readable summary"""
    priority_map = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
    status_map = {1: "Open", 2: "Change Requested", 3: "Closed"}
    impact_map = {1: "Low", 2: "Medium", 3: "High"}

    subject = problem.get('subject', 'N/A')
    problem_id = problem.get('id', 'N/A')
    priority = priority_map.get(problem.get('priority', 0), "Unknown")
    status = status_map.get(problem.get('status', 0), "Unknown")
    impact = impact_map.get(problem.get('impact', 0), "Unknown")
    created_at = problem.get('created_at', 'N/A')

    # Format timestamp if available
    if created_at != 'N/A':
        try:
            dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
            created_at = dt.strftime('%Y-%m-%d %H:%M:%S')
        except:
            pass

    return (
        f"  • Problem #{problem_id}\n"
        f"    Subject: {subject}\n"
        f"    Status: {status} | Priority: {priority} | Impact: {impact}\n"
        f"    Created: {created_at}"
    )


def print_problems_list(problems: List[Dict[str, Any]], max_display: int = 5) -> None:
    """Print a list of problems in a readable format"""
    if not problems:
        print("  No problems found.")
        return

    print(f"  Found {len(problems)} problem(s):\n")
    for i, problem in enumerate(problems[:max_display], 1):
        print(format_problem_summary(problem))
        if i < len(problems[:max_display]):
            print()

    if len(problems) > max_display:
        print(f"\n  ... and {len(problems) - max_display} more problem(s)")


def print_problem_details(problem: Dict[str, Any]) -> None:
    """Print detailed problem information"""
    priority_map = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
    status_map = {1: "Open", 2: "Change Requested", 3: "Closed"}
    impact_map = {1: "Low", 2: "Medium", 3: "High"}

    print(f"  Problem ID: #{problem.get('id', 'N/A')}")
    print(f"  Subject: {problem.get('subject', 'N/A')}")
    print(f"  Status: {status_map.get(problem.get('status', 0), 'Unknown')}")
    print(f"  Priority: {priority_map.get(problem.get('priority', 0), 'Unknown')}")
    print(f"  Impact: {impact_map.get(problem.get('impact', 0), 'Unknown')}")
    print(f"  Known Error: {'Yes' if problem.get('known_error') else 'No'}")

    if problem.get('description_text'):
        desc = problem['description_text']
        print(f"  Description: {desc[:100]}{'...' if len(desc) > 100 else ''}")

    print(f"\n  Requester ID: {problem.get('requester_id', 'N/A')}")
    print(f"  Agent ID: {problem.get('agent_id', 'N/A')}")
    print(f"  Group ID: {problem.get('group_id', 'N/A')}")

    created = problem.get('created_at', 'N/A')
    updated = problem.get('updated_at', 'N/A')
    due_by = problem.get('due_by', 'N/A')

    print(f"\n  Created: {created}")
    print(f"  Updated: {updated}")
    print(f"  Due By: {due_by}")


async def run_examples(freshdesk_data_source: FreshDeskDataSource) -> None:
    """Run all API examples in a single async context"""

    print("\n" + "="*80)
    print("  TICKET OPERATIONS".center(80))
    print("="*80)

    # Example 1: List all tickets
    tickets_response = await freshdesk_data_source.list_tickets(per_page=5)
    print_result("List All Tickets", tickets_response)
    if tickets_response.success and tickets_response.data:
        tickets = tickets_response.data.get('tickets', [])
        print_tickets_list(tickets)

    # Example 2: Create a new ticket
    create_ticket_response = await freshdesk_data_source.create_ticket(
        subject="Test ticket from Python API",
        description="This is a test ticket created via the FreshDesk REST API using Python.",
        email="requester@example.com",
        priority=2,  # Medium priority
        status=2,    # Open
        source=2,    # Portal
    )
    print_result("Create New Ticket", create_ticket_response)
    if create_ticket_response.success and create_ticket_response.data:
        ticket = create_ticket_response.data.get('ticket', {})
        print_ticket_details(ticket)

    # Example 3: Get the created ticket with details
    if create_ticket_response.success and create_ticket_response.data:
        ticket_id = create_ticket_response.data.get('ticket', {}).get('id')
        if ticket_id:
            ticket_response = await freshdesk_data_source.get_ticket(
                id=ticket_id,
                include="conversations,requester"
            )
            print_result(f"Get Ticket Details (ID: {ticket_id})", ticket_response)
            if ticket_response.success and ticket_response.data:
                ticket = ticket_response.data.get('ticket', {})
                print_ticket_details(ticket)

            # Example 4: Add a private note to the ticket
            note_response = await freshdesk_data_source.create_note(
                id=ticket_id,
                body="This is an internal note. Customer reported the issue via email.",
                private=True
            )
            print_result(f"Add Private Note to Ticket {ticket_id}", note_response)
            if note_response.success:
                print("  ✓ Note added successfully")

            # Example 5: Add a public reply to the ticket
            reply_response = await freshdesk_data_source.create_reply(
                id=ticket_id,
                body="Thank you for reporting this issue. Our team is investigating and will update you shortly."
            )
            print_result(f"Add Reply to Ticket {ticket_id}", reply_response)
            if reply_response.success:
                print("  ✓ Reply sent successfully")

            # Example 6: List all conversations for the ticket
            conversations_response = await freshdesk_data_source.list_ticket_conversations(
                id=ticket_id
            )
            print_result(f"List Conversations for Ticket {ticket_id}", conversations_response)
            if conversations_response.success and conversations_response.data:
                conversations = conversations_response.data.get('conversations', [])
                print(f"\n  Found {len(conversations)} conversation(s):\n")
                for conv in conversations[:3]:  # Show first 3
                    conv_id = conv.get('id', 'N/A')
                    body_text = conv.get('body_text', '')
                    is_private = conv.get('private', False)
                    incoming = conv.get('incoming', False)
                    created_at = conv.get('created_at', '')

                    privacy = "Private" if is_private else "Public"
                    direction = "Incoming" if incoming else "Outgoing"

                    print(f"  • Conversation #{conv_id}")
                    print(f"    Type: {privacy} | Direction: {direction}")
                    print(f"    Created: {created_at[:10] if created_at else 'N/A'}")
                    if body_text:
                        print(f"    Content: {body_text[:80]}{'...' if len(body_text) > 80 else ''}")
                    print()

                if len(conversations) > 3:
                    print(f"  ... and {len(conversations) - 3} more conversation(s)\n")

    print("\n" + "="*80)
    print("  PROBLEM OPERATIONS".center(80))
    print("="*80)

    # Example 7: List all problems
    problems_response = await freshdesk_data_source.list_problems(per_page=5)
    print_result("List All Problems", problems_response)
    if problems_response.success and problems_response.data:
        problems = problems_response.data.get('problems', [])
        print_problems_list(problems)

    # Example 8: Create a new problem
    # Note: Get requester_id from the first ticket if available
    requester_id = None
    if tickets_response.success and tickets_response.data:
        tickets = tickets_response.data.get('tickets', [])
        if tickets:
            requester_id = tickets[0].get('requester_id')

    create_problem_response = await freshdesk_data_source.create_problem(
        subject="Server Performance Degradation",
        description="Multiple reports of slow response times from production servers.",
        requester_id=requester_id,  # Required field
        priority=3,  # High priority
        status=1,    # Open
        impact=2,    # Medium impact
        known_error=False,
    )
    print_result("Create New Problem", create_problem_response)
    if create_problem_response.success and create_problem_response.data:
        problem = create_problem_response.data.get('problem', {})
        print_problem_details(problem)

    # Example 9: Get the created problem
    if create_problem_response.success and create_problem_response.data:
        problem_id = create_problem_response.data.get('problem', {}).get('id')
        if problem_id:
            problem_response = await freshdesk_data_source.get_problem(id=problem_id)
            print_result(f"Get Problem Details (ID: {problem_id})", problem_response)
            if problem_response.success and problem_response.data:
                problem = problem_response.data.get('problem', {})
                print_problem_details(problem)

            # Example 10: Create a note on the problem
            note_response = await freshdesk_data_source.create_problem_note(
                id=problem_id,
                body="Investigation started. Checking server logs and resource utilization."
            )
            print_result(f"Add Note to Problem {problem_id}", note_response)
            if note_response.success:
                print("  ✓ Note added successfully")

            # Example 11: Create a task for the problem
            task_response = await freshdesk_data_source.create_problem_task(
                id=problem_id,
                title="Analyze server logs",
                description="Check logs for error patterns and resource bottlenecks",
                status=1,  # Open
            )
            print_result(f"Create Task for Problem {problem_id}", task_response)
            if task_response.success and task_response.data:
                task = task_response.data.get('task', {})
                print(f"  Task ID: #{task.get('id', 'N/A')}")
                print(f"  Title: {task.get('title', 'N/A')}")
                print(f"  Status: {task.get('status', 'N/A')}")

            # Example 12: Update the problem (mark as known error)
            update_response = await freshdesk_data_source.update_problem(
                id=problem_id,
                status=1,
                known_error=True
            )
            print_result(f"Update Problem {problem_id} (Mark as Known Error)", update_response)
            if update_response.success:
                print("  ✓ Problem updated successfully")

    print("\n" + "="*80)
    print("  AGENT OPERATIONS".center(80))
    print("="*80)

    # Example 13: List all agents
    agents_response = await freshdesk_data_source.list_agents(per_page=5, active=True)
    print_result("List All Agents", agents_response)
    if agents_response.success and agents_response.data:
        agents = agents_response.data.get('agents', [])
        print(f"\n  Found {len(agents)} agent(s):\n")
        for agent in agents:
            agent_id = agent.get('id', 'N/A')
            first_name = mask_sensitive_data(agent.get('first_name', ''))
            last_name = mask_sensitive_data(agent.get('last_name', ''))
            email = mask_sensitive_data(agent.get('email', 'N/A'))
            job_title = agent.get('job_title', '')
            occasional = agent.get('occasional', False)
            active = agent.get('active', False)

            agent_type = "Occasional" if occasional else "Full-time"
            status = "Active" if active else "Inactive"

            print(f"  • Agent #{agent_id}: {first_name} {last_name}")
            print(f"    Email: {email}")
            if job_title:
                print(f"    Title: {job_title}")
            print(f"    Type: {agent_type} | Status: {status}")
            print()

    # Example 14: Get agent details
    if agents_response.success and agents_response.data:
        agents = agents_response.data.get('agents', [])
        if agents:
            agent_id = agents[0].get('id')
            if agent_id:
                agent_response = await freshdesk_data_source.view_agent(id=agent_id)
                print_result(f"Get Agent Details (ID: {agent_id})", agent_response)
                if agent_response.success and agent_response.data:
                    agent = agent_response.data.get('agent', {})
                    print("\n  Agent Details:")
                    print(f"    ID: #{agent.get('id', 'N/A')}")
                    print(f"    Job Title: {agent.get('job_title', 'N/A')}")
                    print(f"    Language: {agent.get('language', 'N/A')}")
                    print(f"    Time Zone: {agent.get('time_zone', 'N/A')}")
                    print(f"    Active: {agent.get('active', False)}")
                    print()

    # Example 15: Filter agents by email
    filter_response = await freshdesk_data_source.filter_agents(
        query="email:'*@example.com'"
    )
    print_result("Filter Agents by Email Domain", filter_response)
    if filter_response.success and filter_response.data:
        filtered_agents = filter_response.data.get('agents', [])
        print(f"\n  Found {len(filtered_agents)} agent(s) with @example.com email\n")
        for agent in filtered_agents[:3]:  # Show first 3
            print(f"  • {mask_sensitive_data(agent.get('first_name', ''))} {mask_sensitive_data(agent.get('last_name', ''))} - {mask_sensitive_data(agent.get('email', 'N/A'))}")
        if len(filtered_agents) > 3:
            print(f"  ... and {len(filtered_agents) - 3} more agent(s)\n")

    # Example 16: List all agent fields
    fields_response = await freshdesk_data_source.list_agent_fields()
    print_result("List All Agent Fields", fields_response)
    if fields_response.success and fields_response.data:
        agent_fields = fields_response.data.get('agent_fields', [])
        print(f"\n  Found {len(agent_fields)} agent field(s):\n")
        for field in agent_fields[:5]:  # Show first 5
            field_name = field.get('name', 'N/A')
            label = field.get('label_for_admins', 'N/A')
            field_type = field.get('type', 'N/A')
            mandatory = field.get('mandatory_for_admins', False)
            default = field.get('default_field', False)

            field_info = "Default" if default else "Custom"
            required = "Required" if mandatory else "Optional"

            print(f"  • {field_name}")
            print(f"    Label: {label}")
            print(f"    Type: {field_type} | {field_info} | {required}")
            print()

        if len(agent_fields) > 5:
            print(f"  ... and {len(agent_fields) - 5} more field(s)\n")

    # ========== SOFTWARE EXAMPLES ==========

    # Example 17: List all software
    print_header("Example 17: List All Software/Applications")
    software_list_response = await freshdesk_data_source.list_software()
    print_result("List All Software", software_list_response)
    if software_list_response.success and software_list_response.data:
        software_items = software_list_response.data.get('application', [])
        print(f"\n  Found {len(software_items)} software application(s):\n")
        for software in software_items[:5]:  # Show first 5
            print(f"  • {software.get('name', 'N/A')} (ID: {software.get('id', 'N/A')})")
            print(f"    Type: {software.get('application_type', 'N/A')}")
            print(f"    Status: {software.get('status', 'N/A')}")
            print(f"    Users: {software.get('user_count', 0)} | Installations: {software.get('installation_count', 0)}")
            print(f"    Category: {software.get('category', 'N/A')}")
            print(f"    Managed By: {software.get('managed_by_id', 'N/A')}")
            print()
        if len(software_items) > 5:
            print(f"  ... and {len(software_items) - 5} more software application(s)\n")

    # Example 18: View software details (using first software from list)
    if software_list_response.success and software_list_response.data:
        software_items = software_list_response.data.get('application', [])
        if software_items:
            first_software = software_items[0]
            software_id = first_software.get('id')

            print_header(f"Example 18: View Software Details (ID: {software_id})")
            software_response = await freshdesk_data_source.view_software(id=software_id)
            print_result(f"View Software #{software_id}", software_response)
            if software_response.success and software_response.data:
                software = software_response.data.get('application', {})
                print("\n  Software Details:\n")
                print(f"    ID: #{software.get('id', 'N/A')}")
                print(f"    Name: {software.get('name', 'N/A')}")
                print(f"    Description: {software.get('description', 'N/A')}")
                print(f"    Application Type: {software.get('application_type', 'N/A')}")
                print(f"    Status: {software.get('status', 'N/A')}")
                print(f"    Publisher: {software.get('publisher_id', 'N/A')}")
                print(f"    Category: {software.get('category', 'N/A')}")
                print(f"    Source: {software.get('source', 'N/A')}")
                print(f"    User Count: {software.get('user_count', 0)}")
                print(f"    Installation Count: {software.get('installation_count', 0)}")
                print(f"    Managed By: {software.get('managed_by_id', 'N/A')}")
                print(f"    Notes: {software.get('notes', 'N/A')}")
                print(f"    Created: {software.get('created_at', 'N/A')}")
                print(f"    Updated: {software.get('updated_at', 'N/A')}")
                print()

    # Example 19: List software users (using first software from list)
    if software_list_response.success and software_list_response.data:
        software_items = software_list_response.data.get('application', [])
        if software_items:
            first_software = software_items[0]
            software_id = first_software.get('id')

            print_header(f"Example 19: List Software Users (ID: {software_id})")
            users_response = await freshdesk_data_source.list_software_users(id=software_id)
            print_result(f"List Users of Software #{software_id}", users_response)
            if users_response.success and users_response.data:
                software_users = users_response.data.get('application_users', [])
                print(f"\n  Found {len(software_users)} user(s) using this software:\n")
                for user in software_users[:5]:  # Show first 5
                    print(f"  • User ID: {user.get('user_id', 'N/A')}")
                    print(f"    License ID: {user.get('license_id', 'N/A')}")
                    print(f"    Allocated Date: {user.get('allocated_date', 'N/A')}")
                    print(f"    First Used: {user.get('first_used', 'N/A')}")
                    print(f"    Last Used: {user.get('last_used', 'N/A')}")
                    print(f"    Source: {user.get('source', 'N/A')}")
                    print()
                if len(software_users) > 5:
                    print(f"  ... and {len(software_users) - 5} more user(s)\n")

    # Example 20: List software installations (using first software from list)
    if software_list_response.success and software_list_response.data:
        software_items = software_list_response.data.get('application', [])
        if software_items:
            first_software = software_items[0]
            software_id = first_software.get('id')

            print_header(f"Example 20: List Software Installations (ID: {software_id})")
            installations_response = await freshdesk_data_source.list_software_installations(id=software_id)
            print_result(f"List Installations of Software #{software_id}", installations_response)
            if installations_response.success and installations_response.data:
                installations = installations_response.data.get('installations', [])
                print(f"\n  Found {len(installations)} installation(s):\n")
                for installation in installations[:5]:  # Show first 5
                    print(f"  • Installation ID: {installation.get('id', 'N/A')}")
                    print(f"    Device ID: {installation.get('installation_machine_id', 'N/A')}")
                    print(f"    User: {installation.get('user_id', 'N/A')}")
                    print(f"    Department: {installation.get('department_id', 'N/A')}")
                    print(f"    Path: {installation.get('installation_path', 'N/A')}")
                    print(f"    Version: {installation.get('version', 'N/A')}")
                    print(f"    Installed: {installation.get('installation_date', 'N/A')}")
                    print()
                if len(installations) > 5:
                    print(f"  ... and {len(installations) - 5} more installation(s)\n")


def main() -> None:
    """Example usage of FreshDesk DataSource    
    Before running this example:
    1. Set FRESHDESK_DOMAIN environment variable (e.g., 'company.freshdesk.com')
    2. Set FRESHDESK_API_KEY environment variable with your API key    
    You can obtain an API key from:
    FreshDesk Admin > Profile Settings > API Key
    """
    domain = os.getenv("FRESHDESK_DOMAIN")
    api_key = os.getenv("FRESHDESK_API_KEY")

    if not domain:
        raise Exception("FRESHDESK_DOMAIN is not set")
    if not api_key:
        raise Exception("FRESHDESK_API_KEY is not set")

    print("\n" + "="*80)
    print("  FreshDesk API Integration Example".center(80))
    print("="*80)

    # Build FreshDesk client with API key configuration
    freshdesk_client: FreshDeskClient = FreshDeskClient.build_with_api_key_config(
        FreshDeskApiKeyConfig(
            domain=domain,
            api_key=api_key,
            ssl=True  # Set to False if using self-signed certificates
        ),
    )

    print("\n✓ Connected to FreshDesk")
    print(f"  Domain: {freshdesk_client.get_domain()}")
    print(f"  Base URL: {freshdesk_client.get_base_url()}")

    # Initialize DataSource
    freshdesk_data_source = FreshDeskDataSource(freshdesk_client)

    print("\n" + "="*80)
    print("  Running API Examples...".center(80))
    print("="*80 + "\n")

    # Run all examples in a single async context
    asyncio.run(run_examples(freshdesk_data_source))

    # Summary
    print("\n" + "="*80)
    print("  Example Completed Successfully!".center(80))
    print("="*80)
    print("\nFor more information, see:")
    print("  • FreshService API Docs: https://api.freshservice.com/v2/")
    print("  • FreshDesk API Docs: https://developers.freshdesk.com/api/")
    print()


if __name__ == "__main__":
    main()
