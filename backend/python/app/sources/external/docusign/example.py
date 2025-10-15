
# ruff: noqa
import asyncio
import os

from app.sources.client.docusign.docusign import DocuSignClient, DocuSignPATConfig
from app.sources.external.docusign.docusign import DocuSignDataSource

ACCESS_TOKEN = os.getenv("DOCUSIGN_TOKEN")

async def main() -> None:
    # DocuSign requires both token and account ID
    config = DocuSignPATConfig(
        access_token=ACCESS_TOKEN,
    )
    
    client = await DocuSignClient.build_with_config(config)
    data_source = DocuSignDataSource(client)
    
    # List envelopes for the account
    print("\nListing recent envelopes:")
    envelopes = await data_source.envelopes_list_statuses(accountId="", status="sent", count=10)
    print(envelopes.data)
    
    # Get user information  
    print("\nGetting user information:")
    user_info = await data_source.users_get_user(userId="")
    print(user_info.data)

if __name__ == "__main__":
    asyncio.run(main())