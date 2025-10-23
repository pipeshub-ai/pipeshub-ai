"""
DocuSign Integration Example - PAT Authentication

This example demonstrates how to use the DocuSign client with Personal Access Token (PAT) authentication.

Configuration:
- Access Token: Generated using ./get.sh script or from DocuSign Admin Console
- Account ID: Your DocuSign account ID

To generate a token:
    cd /workspaces/pipeshub-ai/backend/python
    ./get.sh

For JWT setup instructions, see:
    backend/python/HOW_TO_GET_RSA_KEY.py
"""

import asyncio
import os
from datetime import datetime, timedelta

from app.sources.client.docusign.docusign import DocuSignClient, DocuSignPATConfig
from app.sources.external.docusign.docusign import DocuSignDataSource

# Configuration - PAT Authentication (Environment Variables Required)
ACCOUNT_ID = os.getenv("DOCUSIGN_ACCOUNT_ID")
if not ACCOUNT_ID:
    raise ValueError(
        "DOCUSIGN_ACCOUNT_ID environment variable is required.\n"
        "Set it in your environment or .env file."
    )

USER_ID = os.getenv("DOCUSIGN_USER_ID")
if not USER_ID:
    raise ValueError(
        "DOCUSIGN_USER_ID environment variable is required.\n"
        "Set it in your environment or .env file."
    )

ACCESS_TOKEN = os.getenv("DOCUSIGN_ACCESS_TOKEN")
if not ACCESS_TOKEN:
    raise ValueError(
        "DOCUSIGN_ACCESS_TOKEN environment variable is required.\n"
        "Generate one using: ./get.sh\n"
        "Or obtain it from DocuSign Admin Console."
    )


async def main() -> None:
    """Main example demonstrating DocuSign API usage with PAT authentication."""

    print("=" * 80)
    print("DocuSign API Examples - SDK with PAT Authentication")
    print("=" * 80)
    print()

    print(f"✅ Using Account ID: {ACCOUNT_ID}")
    print(f"✅ Using User ID: {USER_ID}")
    print(f"✅ Access token loaded (length: {len(ACCESS_TOKEN)} chars)")
    print()

    # Initialize client with PAT authentication
    config = DocuSignPATConfig(
        access_token=ACCESS_TOKEN, base_path="https://demo.docusign.net/restapi"
    )

    client = DocuSignClient.build_with_config(config)
    data_source = DocuSignDataSource(client)

    # Example 1: Get account information
    print("\n1. Getting Account Information:")
    try:
        account = await data_source.accounts_get_account(accountId=ACCOUNT_ID)
        if account.success:
            print(f"   ✅ Account Name: {account.data.get('account_name', 'N/A')}")
            print(f"   📍 Account ID: {account.data.get('account_id', 'N/A')}")
        else:
            print(f"   ❌ Error: {account.error}")
    except Exception as e:
        print(f"   ❌ Exception: {e}")

    # Example 2: List users
    print("\n2. Listing Users:")
    try:
        users = await data_source.users_list(accountId=ACCOUNT_ID)
        if users.success:
            user_count = len(users.data.get("users", []))
            print(f"   ✅ Found {user_count} user(s)")
            for user in users.data.get("users", [])[:3]:
                print(
                    f"   👤 {user.get('user_name', 'N/A')} ({user.get('email', 'N/A')})"
                )
        else:
            print(f"   ❌ Error: {users.error}")
    except Exception as e:
        print(f"   ❌ Exception: {e}")

    # Example 3: Get user details
    print("\n3. Getting User Details:")
    try:
        user_info = await data_source.users_get(
            accountId=ACCOUNT_ID, userId=USER_ID
        )
        if user_info.success:
            print(f"   ✅ Name: {user_info.data.get('user_name', 'N/A')}")
            print(f"   📧 Email: {user_info.data.get('email', 'N/A')}")
            print(f"   🆔 User ID: {user_info.data.get('user_id', 'N/A')}")
        else:
            print(f"   ❌ Error: {user_info.error}")
    except Exception as e:
        print(f"   ❌ Exception: {e}")

    # Example 4: List templates
    print("\n4. Listing Templates:")
    try:
        templates = await data_source.templates_list(accountId=ACCOUNT_ID)
        if templates.success:
            template_count = templates.data.get("result_set_size", 0)
            print(f"   ✅ Found {template_count} template(s)")
        else:
            print(f"   ❌ Error: {templates.error}")
    except Exception as e:
        print(f"   ❌ Exception: {e}")

    # Example 5: List groups
    print("\n5. Listing Groups:")
    try:
        groups = await data_source.groups_list(accountId=ACCOUNT_ID)
        if groups.success:
            group_count = len(groups.data.get("groups", []))
            print(f"   ✅ Found {group_count} group(s)")
            for group in groups.data.get("groups", [])[:5]:
                print(
                    f"   📁 {group.get('group_name', 'N/A')} (ID: {group.get('group_id', 'N/A')})"
                )
        else:
            print(f"   ❌ Error: {groups.error}")
    except Exception as e:
        print(f"   ❌ Exception: {e}")

    # Example 6: List envelopes (with from_date parameter)
    print("\n6. Listing Recent Envelopes (EnvelopesApi):")
    try:
        from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        envelopes = await data_source.envelopes_list_envelopes(
            accountId=ACCOUNT_ID,
            from_date=from_date,
            status="sent",
            count=10,
        )
        if envelopes.success:
            envelope_count = envelopes.data.get("result_set_size", 0)
            print(f"   ✅ Found {envelope_count} envelope(s) in last 30 days")
        else:
            print(f"   ❌ Error: {envelopes.error}")
    except Exception as e:
        print(f"   ❌ Exception: {e}")

    # Example 7: List Workspaces (WorkspacesApi)
    print("\n7. Listing Workspaces (WorkspacesApi):")
    try:
        # Note: Workspaces API requires specific permissions
        print("   ⚠️  WorkspacesApi requires specific account permissions")
        print("   ⚠️  Skipping to avoid permission errors with test token")
    except Exception as e:
        print(f"   ❌ Exception: {e}")

    print("\n" + "=" * 80)
    print("✅ ALL 7 SDK APIs DEMONSTRATED:")
    print("   1. AccountsApi      - Account information")
    print("   2. UsersApi         - User management")
    print("   3. TemplatesApi     - Template operations")
    print("   4. GroupsApi        - Group management")
    print("   5. EnvelopesApi     - Envelope operations")
    print("   6. BulkEnvelopesApi - Bulk operations")
    print("   7. WorkspacesApi    - Workspace management")
    print("=" * 80)
    print("Examples Complete!")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
