
# ruff: noqa
import asyncio
import os

from app.sources.client.docusign.docusign import DocuSignClient, DocuSignPATConfig
from app.sources.external.docusign.docusign import DocuSignDataSource

ACCESS_TOKEN = os.getenv("DOCUSIGN_TOKEN")

async def main() -> None:
    # DocuSign requires both token and account ID
    config = DocuSignPATConfig(
        access_token="eyJ0eXAiOiJNVCIsImFsZyI6IlJTMjU2Iiwia2lkIjoiNjgxODVmZjEtNGU1MS00Y2U5LWFmMWMtNjg5ODEyMjAzMzE3In0.AQoAAAABAAUABwCAFz9urwveSAgAgFdifPIL3kgCADxQXh4gvfBGpa0EPLR9Aj0VAAEAAAAYAAIAAAAFAAAAHQAAAA0AJAAAADQxNDQwM2Y4LWI4NjEtNDA5Mi04OWEzLTI3YWU3YmRlMzRjOSIAJAAAADQxNDQwM2Y4LWI4NjEtNDA5Mi04OWEzLTI3YWU3YmRlMzRjOTAAAKPlv6sL3kg3AGIUTtHcSgRGpAvF6U7yEqI.nCt9SMUjLXN2HdiP6keA0jZ9YcDoU8PRPRtgFkPRIpo6eFz9aeiAPFSXKZcb28l2rOqDdQUC01emZBgOT8ne27PHc7mUKISk2A8VCRJYxlVXrA8JlomgFvsyqnz_FmNOSjvDIAZwyyv2OVoiQGmupAJ7b9aUPQtHOl3BrNnsQO1jwKkn38SyDDKabIJgZEkC9DMLgiJKgC8QdGO_Eh-zduk0KTRTlMXL60dMQ8n3MxMMEd6fN6mShxkw6Lwc6g9wM-UXaqJeYYENlkpwCX2DGGX3FF6H_hOZcUfwfmNpNxZ9TS3vJixWLpzVRdTJ7ACpxFlBG_TeN9jD9P5P9i2-Lg",
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