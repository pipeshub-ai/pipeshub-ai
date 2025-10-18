# ruff: noqa
import asyncio
import os

from app.sources.client.zammad.zammad import ZammadClient, ZammadTokenConfig
from app.sources.external.zammad.zammad import ZammadDataSource

# Read credentials from environment variables
API_TOKEN = os.getenv("ZAMMAD_TOKEN")
BASE_URL = os.getenv("ZAMMAD_BASE_URL")  # e.g., "https://company.zammad.com"

async def main() -> None:
    if not API_TOKEN:
        raise Exception("ZAMMAD_TOKEN is not set")
    if not BASE_URL:
        raise Exception("ZAMMAD_BASE_URL is not set")
    
    # Create client configuration
    config = ZammadTokenConfig(
        token=API_TOKEN,
        base_url=BASE_URL
    )
    
    # Build client with config
    client = ZammadClient.build_with_config(config)
    
    # Create data source
    data_source = ZammadDataSource(client)
    
    # Call a simple API endpoint
    print("Getting current user:")
    response = await data_source.get_current_user()
    
    current_user_id = None
    print(f"Success: {response.success}")
    if response.success and response.data:
        user = response.data if isinstance(response.data, dict) else response.data.get('user', {})
        print(f"User: {user.get('firstname', '')} {user.get('lastname', '')} ({user.get('email', 'N/A')})")
        print(f"ID: {user.get('id')}")
        current_user_id = user.get('id')
    else:
        print(f"Error: {response.error}")
    
    # Fetch and print tickets
    print("\nFetching tickets:")
    tickets_response = await data_source.list_tickets(per_page=5)
    
    print(f"Success: {tickets_response.success}")
    if tickets_response.success and tickets_response.data:
        tickets = tickets_response.data if isinstance(tickets_response.data, list) else tickets_response.data.get('tickets', [])
        print(f"Found {len(tickets)} ticket(s)")
        for idx, ticket in enumerate(tickets, 1):
            print(f"\n  Ticket {idx}:")
            print(f"    ID: {ticket.get('id')}")
            print(f"    Title: {ticket.get('title')}")
            print(f"    State: {ticket.get('state')}")
            print(f"    Priority: {ticket.get('priority')}")
            print(f"    Created: {ticket.get('created_at')}")
    else:
        print(f"Error: {tickets_response.error}")
    
    # Create a new ticket
    print("\nCreating a new ticket:")
    if not current_user_id:
        print("Skipping ticket creation - no user ID available")
        return
    
    create_response = await data_source.create_ticket(
        title="Test Ticket from API",
        group="Users",
        customer_id=current_user_id,
        article={
            "subject": "Test Ticket from API",
            "body": "This is a test ticket created via the Zammad API",
            "type": "note",
            "internal": False
        },
        state="new",
        priority="2 normal"
    )
    
    print(f"Success: {create_response.success}")
    if create_response.success and create_response.data:
        new_ticket = create_response.data if isinstance(create_response.data, dict) else {}
        print(f"✓ Created Ticket ID: {new_ticket.get('id')}")
        print(f"  Title: {new_ticket.get('title')}")
        print(f"  Number: {new_ticket.get('number')}")
        print(f"  State ID: {new_ticket.get('state_id')}")
        print(f"  Priority ID: {new_ticket.get('priority_id')}")
    else:
        print(f"✗ Failed to create ticket")
        print(f"  Error: {create_response.error}")
        if create_response.data and isinstance(create_response.data, dict):
            print(f"  Details: {create_response.data.get('error', 'No details')}")

    

if __name__ == "__main__":
    asyncio.run(main())