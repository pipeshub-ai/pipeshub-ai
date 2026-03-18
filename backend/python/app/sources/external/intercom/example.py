# ruff: noqa

"""
Intercom API Usage Examples

This example demonstrates how to use the Intercom DataSource to interact with
the Intercom API, covering:
- Authentication (OAuth2, Access Token)
- Initializing the Client and DataSource
- Listing admins, contacts, conversations
- Contact CRUD and search
- Companies, articles, teams, tags, segments

Prerequisites:
For OAuth2:
1. Create an Intercom app at https://developers.intercom.com/
2. Set INTERCOM_CLIENT_ID and INTERCOM_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For Access Token:
1. Go to your Intercom Developer Hub
2. Copy the access token from your app settings
3. Set INTERCOM_ACCESS_TOKEN environment variable

OAuth Endpoints:
- Auth: https://app.intercom.com/oauth
- Token: https://api.intercom.io/auth/eagle/token
- Auth Method: body
"""

import asyncio
import json
import os

from app.sources.client.intercom.intercom import (
    IntercomClient,
    IntercomOAuthConfig,
    IntercomResponse,
    IntercomTokenConfig,
)
from app.sources.external.intercom.intercom import IntercomDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
CLIENT_ID = os.getenv("INTERCOM_CLIENT_ID")
CLIENT_SECRET = os.getenv("INTERCOM_CLIENT_SECRET")
ACCESS_TOKEN = os.getenv("INTERCOM_ACCESS_TOKEN")
REDIRECT_URI = os.getenv("INTERCOM_REDIRECT_URI", "http://localhost:8080/callback")


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: IntercomResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            if isinstance(data, dict):
                for key in ("admins", "data", "contacts", "conversations",
                            "companies", "articles", "teams", "tags",
                            "segments", "data_attributes"):
                    if key in data:
                        items = data[key]
                        if isinstance(items, list):
                            print(f"   Found {len(items)} {key}.")
                            if items:
                                print(f"   Sample: {json.dumps(items[0], indent=2, default=str)[:400]}...")
                        return
                print(f"   Data: {json.dumps(data, indent=2, default=str)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Intercom Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("  Starting OAuth flow...")
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://app.intercom.com/oauth",
                token_endpoint="https://api.intercom.io/auth/eagle/token",
                redirect_uri=REDIRECT_URI,
                scopes=[],
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = IntercomOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Access Token
    if config is None and ACCESS_TOKEN:
        print("  Using Access Token authentication")
        config = IntercomTokenConfig(access_token=ACCESS_TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - INTERCOM_CLIENT_ID and INTERCOM_CLIENT_SECRET (for OAuth2)")
        print("   - INTERCOM_ACCESS_TOKEN (for Access Token)")
        return

    client = IntercomClient.build_with_config(config)
    data_source = IntercomDataSource(client)
    print("  Client initialized successfully.")

    try:
        # 2. Get Current Admin
        print_section("Current Admin (Me)")
        me_resp = await data_source.get_me()
        print_result("Get Me", me_resp)

        # 3. List Admins
        print_section("Admins")
        admins_resp = await data_source.list_admins()
        print_result("List Admins", admins_resp)

        # 4. List Contacts
        print_section("Contacts")
        contacts_resp = await data_source.list_contacts(per_page=5)
        print_result("List Contacts", contacts_resp)

        # Get first contact details
        if contacts_resp.success and contacts_resp.data:
            contacts_data = contacts_resp.data.get("data", [])
            if contacts_data:
                contact_id = contacts_data[0].get("id")
                if contact_id:
                    print_section(f"Contact Details (ID: {contact_id})")
                    contact_resp = await data_source.get_contact(id=contact_id)
                    print_result("Get Contact", contact_resp)

        # 5. List Conversations
        print_section("Conversations")
        convs_resp = await data_source.list_conversations(per_page=5)
        print_result("List Conversations", convs_resp)

        # Get first conversation details
        if convs_resp.success and convs_resp.data:
            convs_data = convs_resp.data.get("conversations", [])
            if convs_data:
                conv_id = convs_data[0].get("id")
                if conv_id:
                    print_section(f"Conversation Details (ID: {conv_id})")
                    conv_resp = await data_source.get_conversation(id=conv_id)
                    print_result("Get Conversation", conv_resp)

        # 6. List Companies
        print_section("Companies")
        companies_resp = await data_source.list_companies(per_page=5)
        print_result("List Companies", companies_resp)

        # 7. List Articles
        print_section("Articles")
        articles_resp = await data_source.list_articles(per_page=5)
        print_result("List Articles", articles_resp)

        # 8. List Teams
        print_section("Teams")
        teams_resp = await data_source.list_teams()
        print_result("List Teams", teams_resp)

        # 9. List Tags
        print_section("Tags")
        tags_resp = await data_source.list_tags()
        print_result("List Tags", tags_resp)

        # 10. List Segments
        print_section("Segments")
        segments_resp = await data_source.list_segments()
        print_result("List Segments", segments_resp)

        # 11. List Data Attributes
        print_section("Data Attributes")
        attrs_resp = await data_source.list_data_attributes()
        print_result("List Data Attributes", attrs_resp)

    finally:
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Intercom API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
