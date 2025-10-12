
import asyncio
from app.sources.client.hubspot.hubspot import HubSpotClient
from app.sources.external.hubspot.hubspot import HubSpotDataSource

async def simple_test():
    """Simple test without complex path manipulation"""
    access_token = " "  
    
    client = HubSpotClient.build_with_token(access_token)
    data_source = HubSpotDataSource(client)
    
    account_info = await data_source.get_account_info()
    print(f"✅ Account connected: {account_info.success}")
    
    contacts = await data_source.get_contacts(limit=3)
    if contacts.success:
        print(f"📋 Found {contacts.data['total']} contacts:")
        for contact in contacts.data['contacts']:
            props = contact['properties']
            name = f"{props.get('firstname', 'N/A')} {props.get('lastname', 'N/A')}"
            email = props.get('email', 'N/A')
            print(f"   • {name} ({email})")
    
    companies = await data_source.get_companies(limit=2)
    if companies.success:
        print(f"🏢 Found {companies.data['total']} companies:")
        for company in companies.data['companies']:
            name = company['properties'].get('name', 'N/A')
            domain = company['properties'].get('domain', 'N/A')
            print(f"   • {name} ({domain})")

if __name__ == "__main__":
    asyncio.run(simple_test())
