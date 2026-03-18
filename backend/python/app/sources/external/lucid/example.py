# ruff: noqa

"""
Lucid API Usage Examples

This example demonstrates how to use the Lucid DataSource to interact with
the Lucid API (v1), covering:
- Authentication (OAuth2, Bearer Token)
- Initializing the Client and DataSource
- Fetching User Details
- Listing Documents and Folders
- Working with Pages and Data Sources

Prerequisites:
For OAuth2:
1. Create a Lucid OAuth app at https://developer.lucid.co/
2. Set LUCID_CLIENT_ID and LUCID_CLIENT_SECRET environment variables
3. The OAuth flow will automatically open a browser for authorization

For Bearer Token:
1. Generate an API token from Lucid developer settings
2. Set LUCID_API_TOKEN environment variable

OAuth Scopes:
lucidchart.document.app:read, lucidchart.document.app:write, user.profile
"""

import asyncio
import json
import os

from app.sources.client.lucid.lucid import (
    LucidClient,
    LucidOAuthConfig,
    LucidTokenConfig,
    LucidResponse,
)
from app.sources.external.lucid.lucid import LucidDataSource
from app.sources.external.utils.oauth import perform_oauth_flow

# --- Configuration ---
# OAuth2 credentials (highest priority)
CLIENT_ID = os.getenv("LUCID_CLIENT_ID")
CLIENT_SECRET = os.getenv("LUCID_CLIENT_SECRET")

# Bearer Token (second priority)
API_TOKEN = os.getenv("LUCID_API_TOKEN")

# OAuth redirect URI
REDIRECT_URI = os.getenv("LUCID_REDIRECT_URI", "http://localhost:8080/callback")

# OAuth scopes
SCOPES = [
    "lucidchart.document.app:read",
    "lucidchart.document.app:write",
    "user.profile",
]


def print_section(title: str):
    print(f"\n{'-'*80}")
    print(f"| {title}")
    print(f"{'-'*80}")


def print_result(name: str, response: LucidResponse, show_data: bool = True):
    if response.success:
        print(f"  {name}: Success")
        if show_data and response.data:
            data = response.data
            # Handle list-type responses
            for key in ("documents", "folders", "pages", "users", "dataSources"):
                if isinstance(data, dict) and key in data:
                    items = data[key]
                    print(f"   Found {len(items)} {key}.")
                    if items:
                        print(f"   Sample: {json.dumps(items[0], indent=2)[:400]}...")
                    return
            # Generic response
            print(f"   Data: {json.dumps(data, indent=2)[:500]}...")
    else:
        print(f"  {name}: Failed")
        print(f"   Error: {response.error}")
        if response.message:
            print(f"   Message: {response.message}")


async def main() -> None:
    # 1. Initialize Client
    print_section("Initializing Lucid Client")

    config = None

    # Priority 1: OAuth2
    if CLIENT_ID and CLIENT_SECRET:
        print("  Using OAuth2 authentication")
        try:
            print("Starting OAuth flow...")
            token_response = perform_oauth_flow(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                auth_endpoint="https://lucid.app/oauth2/authorize",
                token_endpoint="https://api.lucid.co/oauth2/token",
                redirect_uri=REDIRECT_URI,
                scopes=SCOPES,
                scope_delimiter=" ",
                auth_method="body",
            )

            access_token = token_response.get("access_token")
            if not access_token:
                raise Exception("No access_token found in OAuth response")

            config = LucidOAuthConfig(
                access_token=access_token,
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
            )
            print("  OAuth authentication successful")
        except Exception as e:
            print(f"  OAuth flow failed: {e}")
            print("  Falling back to other authentication methods...")

    # Priority 2: Bearer Token
    if config is None and API_TOKEN:
        print("  Using Bearer Token authentication")
        config = LucidTokenConfig(token=API_TOKEN)

    if config is None:
        print("  No valid authentication method found.")
        print("   Please set one of the following:")
        print("   - LUCID_CLIENT_ID and LUCID_CLIENT_SECRET (for OAuth2)")
        print("   - LUCID_API_TOKEN (for Bearer Token)")
        return

    client = LucidClient.build_with_config(config)
    data_source = LucidDataSource(client)
    print("Client initialized successfully.")

    try:
        # 2. Get Current User
        print_section("Current User")
        user_resp = await data_source.get_current_user()
        print_result("Get Current User", user_resp)

        # 3. List Documents
        print_section("Documents")
        docs_resp = await data_source.list_documents(pageSize=10)
        print_result("List Documents", docs_resp)

        # Extract first document ID for further exploration
        document_id = None
        if docs_resp.success and docs_resp.data:
            documents = docs_resp.data.get("documents", [])
            if not documents and isinstance(docs_resp.data, list):
                documents = docs_resp.data
            if documents:
                document_id = str(documents[0].get("id") or documents[0].get("documentId", ""))
                print(f"   Using Document ID: {document_id}")

        if document_id:
            # 4. Get Specific Document
            print_section("Document Details")
            doc_resp = await data_source.get_document(documentId=document_id)
            print_result("Get Document", doc_resp)

            # 5. List Pages in Document
            print_section("Document Pages")
            pages_resp = await data_source.list_pages(documentId=document_id)
            print_result("List Pages", pages_resp)

        # 6. List Folders
        print_section("Folders")
        folders_resp = await data_source.list_folders(pageSize=10)
        print_result("List Folders", folders_resp)

        # Extract first folder ID
        folder_id = None
        if folders_resp.success and folders_resp.data:
            folders = folders_resp.data.get("folders", [])
            if not folders and isinstance(folders_resp.data, list):
                folders = folders_resp.data
            if folders:
                folder_id = str(folders[0].get("id") or folders[0].get("folderId", ""))
                print(f"   Using Folder ID: {folder_id}")

        if folder_id:
            # 7. Get Specific Folder
            print_section("Folder Details")
            folder_resp = await data_source.get_folder(folderId=folder_id)
            print_result("Get Folder", folder_resp)

            # 8. List Folder Documents
            print_section("Folder Documents")
            folder_docs_resp = await data_source.list_folder_documents(folderId=folder_id)
            print_result("List Folder Documents", folder_docs_resp)

        # 9. List Users
        print_section("Users")
        users_resp = await data_source.list_users(pageSize=10)
        print_result("List Users", users_resp)

        # 10. List Data Sources
        print_section("Data Sources")
        ds_resp = await data_source.list_data_sources()
        print_result("List Data Sources", ds_resp)

    finally:
        # Cleanup: Close the HTTP client session
        print("\nClosing client connection...")
        inner_client = client.get_client()
        if hasattr(inner_client, "close"):
            await inner_client.close()

    print("\n" + "=" * 80)
    print("  All Lucid API operations tested!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
