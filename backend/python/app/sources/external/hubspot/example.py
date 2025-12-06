# ruff: noqa
"""Example usage of HubSpot client and data source

This example demonstrates how to:
1. Initialize the HubSpot client with an access token
2. Create a HubSpot data source
3. Perform CRUD operations on contacts, companies, and deals
4. Use search functionality

Prerequisites:
    - Set HUBSPOT_TOKEN environment variable with your HubSpot access token
    - You can get a token from HubSpot by creating a private app or using OAuth

To get a HubSpot access token:
    1. Go to Settings > Integrations > Private Apps in your HubSpot account
    2. Create a new private app
    3. Grant necessary scopes (crm.objects.contacts.read, crm.objects.contacts.write, etc.)
    4. Copy the access token

Run this example:
    export HUBSPOT_TOKEN="your-access-token"
    python example.py
"""

import asyncio
import os
import sys
from typing import Any, Dict

from app.sources.client.hubspot.hubspot_ import HubSpotClient, HubSpotTokenConfig
from app.sources.external.hubspot.hubspot_ import HubSpotDataSource


async def example_contacts(data_source: HubSpotDataSource) -> None:
    """Example operations on contacts"""
    print("\n========== CONTACTS ==========")
    
    try:
        # Get contacts
        print("\n1. Getting contacts...")
        contacts = await data_source.get_contacts(limit=5)
        print(f"✅ Retrieved {len(contacts.get('results', []))} contacts")
        
        # Create a contact
        print("\n2. Creating a new contact...")
        new_contact = await data_source.create_contact(
            properties={
                "email": f"test.{asyncio.get_event_loop().time()}@example.com",
                "firstname": "Test",
                "lastname": "User",
                "phone": "(555) 555-5555",
            }
        )
        contact_id = new_contact.get("id")
        print(f"✅ Created contact with ID: {contact_id}")
        
        # Get contact by ID
        print(f"\n3. Getting contact {contact_id}...")
        contact = await data_source.get_contact_by_id(
            contact_id=contact_id,
            properties=["email", "firstname", "lastname"],
        )
        print(f"✅ Retrieved contact: {contact.get('properties', {}).get('email')}")
        
        # Update contact
        print(f"\n4. Updating contact {contact_id}...")
        updated_contact = await data_source.update_contact(
            contact_id=contact_id,
            properties={"lastname": "Updated"},
        )
        print(f"✅ Updated contact lastname to: {updated_contact.get('properties', {}).get('lastname')}")
        
        # Search contacts
        print("\n5. Searching contacts by email...")
        search_results = await data_source.search_contacts(
            filter_groups=[
                {
                    "filters": [
                        {
                            "propertyName": "email",
                            "operator": "CONTAINS_TOKEN",
                            "value": "*@example.com",
                        }
                    ]
                }
            ],
            properties=["email", "firstname", "lastname"],
            limit=5,
        )
        print(f"✅ Found {search_results.get('total', 0)} contacts matching criteria")
        
        # Clean up - delete the contact
        print(f"\n6. Deleting contact {contact_id}...")
        await data_source.delete_contact(contact_id=contact_id)
        print(f"✅ Deleted contact {contact_id}")
        
    except Exception as e:
        print(f"❌ Error in contacts example: {e}")
        raise


async def example_companies(data_source: HubSpotDataSource) -> None:
    """Example operations on companies"""
    print("\n========== COMPANIES ==========")
    
    try:
        # Get companies
        print("\n1. Getting companies...")
        companies = await data_source.get_companies(limit=5)
        print(f"✅ Retrieved {len(companies.get('results', []))} companies")
        
        # Create a company
        print("\n2. Creating a new company...")
        new_company = await data_source.create_company(
            properties={
                "name": f"Test Company {asyncio.get_event_loop().time()}",
                "domain": "testcompany.com",
                "industry": "Technology",
                "city": "San Francisco",
            }
        )
        company_id = new_company.get("id")
        print(f"✅ Created company with ID: {company_id}")
        
        # Get company by ID
        print(f"\n3. Getting company {company_id}...")
        company = await data_source.get_company_by_id(
            company_id=company_id,
            properties=["name", "domain", "industry"],
        )
        print(f"✅ Retrieved company: {company.get('properties', {}).get('name')}")
        
        # Update company
        print(f"\n4. Updating company {company_id}...")
        updated_company = await data_source.update_company(
            company_id=company_id,
            properties={"city": "New York"},
        )
        print(f"✅ Updated company city to: {updated_company.get('properties', {}).get('city')}")
        
        # Clean up
        print(f"\n5. Deleting company {company_id}...")
        await data_source.delete_company(company_id=company_id)
        print(f"✅ Deleted company {company_id}")
        
    except Exception as e:
        print(f"❌ Error in companies example: {e}")
        raise


async def example_deals(data_source: HubSpotDataSource) -> None:
    """Example operations on deals"""
    print("\n========== DEALS ==========")
    
    try:
        # Get deals
        print("\n1. Getting deals...")
        deals = await data_source.get_deals(limit=5)
        print(f"✅ Retrieved {len(deals.get('results', []))} deals")
        
        # Create a deal
        print("\n2. Creating a new deal...")
        new_deal = await data_source.create_deal(
            properties={
                "dealname": f"Test Deal {asyncio.get_event_loop().time()}",
                "dealstage": "appointmentscheduled",
                "amount": "10000",
                "closedate": "2024-12-31",
            }
        )
        deal_id = new_deal.get("id")
        print(f"✅ Created deal with ID: {deal_id}")
        
        # Get deal by ID
        print(f"\n3. Getting deal {deal_id}...")
        deal = await data_source.get_deal_by_id(
            deal_id=deal_id,
            properties=["dealname", "amount", "dealstage"],
        )
        print(f"✅ Retrieved deal: {deal.get('properties', {}).get('dealname')}")
        
        # Update deal
        print(f"\n4. Updating deal {deal_id}...")
        updated_deal = await data_source.update_deal(
            deal_id=deal_id,
            properties={"amount": "15000"},
        )
        print(f"✅ Updated deal amount to: ${updated_deal.get('properties', {}).get('amount')}")
        
        # Clean up
        print(f"\n5. Deleting deal {deal_id}...")
        await data_source.delete_deal(deal_id=deal_id)
        print(f"✅ Deleted deal {deal_id}")
        
    except Exception as e:
        print(f"❌ Error in deals example: {e}")
        raise


async def example_tickets(data_source: HubSpotDataSource) -> None:
    """Example operations on tickets"""
    print("\n========== TICKETS ==========")
    
    try:
        # Get tickets
        print("\n1. Getting tickets...")
        tickets = await data_source.get_tickets(limit=5)
        print(f"✅ Retrieved {len(tickets.get('results', []))} tickets")
        
        # Create a ticket
        print("\n2. Creating a new ticket...")
        new_ticket = await data_source.create_ticket(
            properties={
                "subject": f"Test Ticket {asyncio.get_event_loop().time()}",
                "content": "This is a test ticket created by the example script",
                "hs_pipeline_stage": "1",
            }
        )
        ticket_id = new_ticket.get("id")
        print(f"✅ Created ticket with ID: {ticket_id}")
        
        # Get ticket by ID
        print(f"\n3. Getting ticket {ticket_id}...")
        ticket = await data_source.get_ticket_by_id(
            ticket_id=ticket_id,
            properties=["subject", "content"],
        )
        print(f"✅ Retrieved ticket: {ticket.get('properties', {}).get('subject')}")
        
        # Clean up
        print(f"\n4. Deleting ticket {ticket_id}...")
        await data_source.delete_ticket(ticket_id=ticket_id)
        print(f"✅ Deleted ticket {ticket_id}")
        
    except Exception as e:
        print(f"❌ Error in tickets example: {e}")
        raise


def main() -> None:
    """Main function to run all examples"""
    # Get token from environment
    token = os.getenv("HUBSPOT_TOKEN")
    if not token:
        print("❌ Error: HUBSPOT_TOKEN environment variable is not set")
        print("\nTo get a HubSpot access token:")
        print("1. Go to Settings > Integrations > Private Apps in your HubSpot account")
        print("2. Create a new private app")
        print("3. Grant necessary scopes (crm.objects.*.read, crm.objects.*.write)")
        print("4. Copy the access token")
        print("\nThen run:")
        print('export HUBSPOT_TOKEN="your-access-token"')
        print("python example.py")
        sys.exit(1)
    
    print("🚀 Starting HubSpot Data Source Examples")
    print("=" * 50)
    
    try:
        # Initialize client
        print("\n📡 Initializing HubSpot client...")
        hubspot_client = HubSpotClient.build_with_config(
            HubSpotTokenConfig(token=token)
        )
        print("✅ HubSpot client initialized")
        
        # Create data source
        print("📊 Creating HubSpot data source...")
        data_source = HubSpotDataSource(hubspot_client)
        print("✅ HubSpot data source created")
        
        # Run examples
        asyncio.run(example_contacts(data_source))
        asyncio.run(example_companies(data_source))
        asyncio.run(example_deals(data_source))
        asyncio.run(example_tickets(data_source))
        
        print("\n" + "=" * 50)
        print("✅ All examples completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
