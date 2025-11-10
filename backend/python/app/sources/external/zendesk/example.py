# ruff: noqa
import asyncio
import os

from app.sources.client.zendesk.zendesk import ZendeskTokenConfig, ZendeskClient
from app.sources.external.zendesk.zendesk import ZendeskDataSource

API_TOKEN = os.getenv("ZENDESK_API_TOKEN")
EMAIL = os.getenv("ZENDESK_EMAIL")
SUBDOMAIN = "pipeshub"  # For https://pipeshub.zendesk.com/


async def main() -> None:
    if not API_TOKEN:
        raise Exception("ZENDESK_API_TOKEN is not set")
    if not EMAIL:
        raise Exception("ZENDESK_EMAIL is not set")

    config = ZendeskTokenConfig(token=API_TOKEN, email=EMAIL, subdomain=SUBDOMAIN)
    client = ZendeskClient.build_with_config(config)
    data_source = ZendeskDataSource(client)

    try:
        # Get current user information
        print("Getting current user:")
        current_user = await data_source.show_current_user()
        print(f"Success: {current_user.success}")
        if current_user.success and current_user.data:
            user_data = current_user.data
            print(
                f"User: {user_data.get('user', {}).get('name')} ({user_data.get('user', {}).get('email')})"
            )
            print(f"Role: {user_data.get('user', {}).get('role')}")
        else:
            print(f"Error: {current_user.error}")

        print("\n" + "=" * 50 + "\n")

        # List recent tickets (first 5)
        print("Getting recent tickets:")
        tickets_response = await data_source.list_tickets(
            sort_by="updated_at", sort_order="desc", per_page=5
        )
        print(f"Success: {tickets_response.success}")
        if tickets_response.success:
            tickets = tickets_response.data.get("tickets", [])
            print(f"Found {len(tickets)} tickets:")
            for ticket in tickets:
                print(
                    f"  - #{ticket.get('id')}: {ticket.get('subject')} [{ticket.get('status')}]"
                )
        else:
            print(f"Error: {tickets_response.error}")

    finally:
        # Clean up the HTTP client session
        await client.get_client().close()


if __name__ == "__main__":
    asyncio.run(main())
