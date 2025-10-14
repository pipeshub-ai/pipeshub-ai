# ruff: noqa
import asyncio
import os

from app.sources.client.docusign.docusign import DocuSignClient, DocuSignTokenConfig
from app.sources.external.docusign.docusign_data_source import DocuSignDataSource

# Get credentials from environment variables
TOKEN_ID = os.getenv("DOCUSIGN_TOKEN_ID")
TOKEN_SECRET = os.getenv("DOCUSIGN_TOKEN_SECRET")
BASE_URL = os.getenv("DOCUSIGN_BASE_URL", "https://demo.docusign.net/restapi")
ACCOUNT_ID = os.getenv("DOCUSIGN_ACCOUNT_ID")

async def main() -> None:
    # Create configuration
    config = DocuSignTokenConfig(
        base_url=BASE_URL,
        token_id=TOKEN_ID,
        token_secret=TOKEN_SECRET
    )
    
    # Build client and data source
    client = DocuSignClient.build_with_config(config)
    data_source = DocuSignDataSource(client)

    # Get user info
    print("\nGetting user info...")
    user_info = await data_source.get_user_info()
    print(f"User info: {user_info}")
    
    # List accounts
    print("\nListing accounts...")
    accounts = await data_source.list_accounts()
    print(f"Accounts: {accounts}")
    
    # Get account information
    if ACCOUNT_ID:
        print(f"\nGetting account information for {ACCOUNT_ID}...")
        account_info = await data_source.get_account_information(account_id=ACCOUNT_ID)
        print(f"Account info: {account_info}")
    
    # List envelopes
    if ACCOUNT_ID:
        print(f"\nListing envelopes for account {ACCOUNT_ID}...")
        envelopes = await data_source.list_envelopes(
            account_id=ACCOUNT_ID,
            from_date="2023-01-01",
            status="completed"
        )
        print(f"Envelopes: {envelopes}")
    
    # List templates
    if ACCOUNT_ID:
        print(f"\nListing templates for account {ACCOUNT_ID}...")
        templates = await data_source.list_templates(account_id=ACCOUNT_ID)
        print(f"Templates: {templates}")
    
    # List users
    if ACCOUNT_ID:
        print(f"\nListing users for account {ACCOUNT_ID}...")
        users = await data_source.list_users(account_id=ACCOUNT_ID)
        print(f"Users: {users}")
        
    # Get API info (utility method)
    print("\nGetting API info...")
    api_info = await data_source.get_api_info()
    print(f"API categories: {api_info.data['api_categories']}")
    print(f"Total methods available: {api_info.data['total_methods']}")

if __name__ == "__main__":
    asyncio.run(main())
