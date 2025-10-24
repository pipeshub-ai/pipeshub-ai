# ruff: noqa
"""
Simple HubSpot API test example.
No pagination, no complexity - just test basic API calls and print results.
"""

import asyncio
import os
from app.sources.client.hubspot.hubspot import HubSpotTokenConfig, HubSpotClient, HubSpotResponse
from app.sources.external.hubspot.hubspot_data_source import HubSpotDataSource


async def main():
    # Replace with your HubSpot private app access token
    TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN")
    print("TOKEN", TOKEN)
    
    if not TOKEN:
        raise Exception("HUBSPOT_ACCESS_TOKEN is not set")
    
    # Create client
    config = HubSpotTokenConfig(token=TOKEN)
    print("config", config)
    
    client = HubSpotClient.build_with_config(config)
    print("client", client)
    
    hubspot = HubSpotDataSource(client)
    print("hubspot", hubspot)
    
    # Test 1: List contacts
    print("\n=== Testing List Contacts ===")
    response: HubSpotResponse = await hubspot.list_contacts(limit=5)
    print("response-----------", response.success)
    if response.data:
        print("response data type-----------", type(response.data))
        print("response data-----------", response.data)
    else:
        print("response data-----------", None)
    print("response error-----------", response.error)
    print("response message-----------", response.message)
    
    # Test 2: List companies
    print("\n=== Testing List Companies ===")
    response: HubSpotResponse = await hubspot.list_companies(limit=5)
    print("response-----------", response.success)
    if response.data:
        print("response data type-----------", type(response.data))
        print("response data-----------", response.data)
    else:
        print("response data-----------", None)
    print("response error-----------", response.error)
    print("response message-----------", response.message)
    
    # Test 3: List deals  
    print("\n=== Testing List Deals ===")
    response: HubSpotResponse = await hubspot.list_deals(limit=5)
    print("response-----------", response.success)
    if response.data:
        print("response data type-----------", type(response.data))
        print("response data-----------", response.data)
    else:
        print("response data-----------", None)
    print("response error-----------", response.error)
    print("response message-----------", response.message)
    
    # Test 4: Create a test contact
    print("\n=== Testing Create Contact ===")
    contact_properties = {
        "firstname": "Test",
        "lastname": "Contact",
        "email": f"test.contact.{int(asyncio.get_event_loop().time())}@example.com",
        "phone": "+1-555-123-4567"
    }
    response: HubSpotResponse = await hubspot.create_contact(properties=contact_properties)
    print("response-----------", response.success)
    if response.data:
        print("response data type-----------", type(response.data))
        print("response data-----------", response.data)
        
        # If contact was created successfully, try to get it
        if response.success and response.data and 'id' in response.data:
            contact_id = response.data['id']
            print(f"\n=== Testing Get Contact (ID: {contact_id}) ===")
            get_response: HubSpotResponse = await hubspot.get_contact(contact_id=contact_id)
            print("get response-----------", get_response.success)
            if get_response.data:
                print("get response data type-----------", type(get_response.data))
                print("get response data-----------", get_response.data)
            else:
                print("get response data-----------", None)
            print("get response error-----------", get_response.error)
            print("get response message-----------", get_response.message)
    else:
        print("response data-----------", None)
    print("response error-----------", response.error)
    print("response message-----------", response.message)
    
    # Test 5: Search contacts
    print("\n=== Testing Search Contacts ===")
    search_filters = [
        {
            "filters": [
                {
                    "propertyName": "email",
                    "operator": "CONTAINS_TOKEN",
                    "value": "example.com"
                }
            ]
        }
    ]
    response: HubSpotResponse = await hubspot.search_contacts(
        filter_groups=search_filters,
        properties=["firstname", "lastname", "email"],
        limit=3
    )
    print("response-----------", response.success)
    if response.data:
        print("response data type-----------", type(response.data))
        print("response data-----------", response.data)
    else:
        print("response data-----------", None)
    print("response error-----------", response.error)
    print("response message-----------", response.message)
    
    # Test 6: List forms
    print("\n=== Testing List Forms ===")
    response: HubSpotResponse = await hubspot.list_forms(limit=3)
    print("response-----------", response.success)
    if response.data:
        print("response data type-----------", type(response.data))
        print("response data-----------", response.data)
    else:
        print("response data-----------", None)
    print("response error-----------", response.error)
    print("response message-----------", response.message)
    
    # Test 7: List contact properties
    print("\n=== Testing List Properties ===")
    response: HubSpotResponse = await hubspot.list_properties(object_type="contacts")
    print("response-----------", response.success)
    if response.data:
        print("response data type-----------", type(response.data))
        # Only print first few properties to avoid too much output
        if isinstance(response.data, dict) and 'results' in response.data:
            results = response.data['results']
            print(f"response data count-----------", len(results))
            if results:
                print("response data first property-----------", results[0])
        else:
            print("response data-----------", response.data)
    else:
        print("response data-----------", None)
    print("response error-----------", response.error)
    print("response message-----------", response.message)


if __name__ == "__main__":
    asyncio.run(main())