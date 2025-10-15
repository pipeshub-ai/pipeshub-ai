
# ruff: noqa
"""
DocuSign Integration Example

This example demonstrates how to use the DocuSign client and data source.
Replace the ACCESS_TOKEN and ACCOUNT_ID with your actual credentials.

To get your credentials:
1. Account ID: Get from OAuth /userinfo endpoint (NOT Integration Key)
2. User ID: Get from OAuth /userinfo endpoint
3. Access Token: Generate from DocuSign admin console or OAuth flow

For testing purposes, you can use the diagnostic tool:
    python -m app.sources.external.docusign.diagnose_oauth
"""
import asyncio
import os

from app.sources.client.docusign import DocuSignClient, DocuSignPATConfig
from app.sources.external.docusign import DocuSignDataSource

# Configuration - Replace with your actual credentials
ACCESS_TOKEN = os.getenv("DOCUSIGN_TOKEN", "your_access_token_here")
ACCOUNT_ID = os.getenv("DOCUSIGN_ACCOUNT_ID", "your_account_id_here")  # From /userinfo, NOT Integration Key
USER_ID = os.getenv("DOCUSIGN_USER_ID", "your_user_id_here")  # From /userinfo


async def main() -> None:
    """Main example demonstrating DocuSign API usage."""
    
    # Initialize client with PAT authentication
    config = DocuSignPATConfig(
        access_token=ACCESS_TOKEN,
    )
    
    client = DocuSignClient.build_with_config(config)
    data_source = DocuSignDataSource(client)
    
    print("=" * 80)
    print("DocuSign API Examples")
    print("=" * 80)
    
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
            user_count = len(users.data.get('users', []))
            print(f"   ✅ Found {user_count} user(s)")
            for user in users.data.get('users', [])[:3]:
                print(f"   👤 {user.get('user_name', 'N/A')} ({user.get('email', 'N/A')})")
        else:
            print(f"   ❌ Error: {users.error}")
    except Exception as e:
        print(f"   ❌ Exception: {e}")
    
    # Example 3: Get user details
    print("\n3. Getting User Details:")
    try:
        user_info = await data_source.users_get(accountId=ACCOUNT_ID, userId=USER_ID)
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
            template_count = templates.data.get('result_set_size', 0)
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
            group_count = len(groups.data.get('groups', []))
            print(f"   ✅ Found {group_count} group(s)")
            for group in groups.data.get('groups', [])[:5]:
                print(f"   📁 {group.get('group_name', 'N/A')} (ID: {group.get('group_id', 'N/A')})")
        else:
            print(f"   ❌ Error: {groups.error}")
    except Exception as e:
        print(f"   ❌ Exception: {e}")
    
    # Example 6: List envelopes (with from_date parameter)
    print("\n6. Listing Recent Envelopes:")
    try:
        from datetime import datetime, timedelta
        from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        
        envelopes = await data_source.envelopes_list_envelopes(
            accountId=ACCOUNT_ID,
            from_date=from_date,
            status="sent,delivered,completed",
            count=10
        )
        if envelopes.success:
            envelope_count = envelopes.data.get('result_set_size', 0)
            print(f"   ✅ Found {envelope_count} envelope(s) in last 30 days")
        else:
            print(f"   ❌ Error: {envelopes.error}")
    except Exception as e:
        print(f"   ❌ Exception: {e}")
    
    print("\n" + "=" * 80)
    print("Examples Complete!")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())