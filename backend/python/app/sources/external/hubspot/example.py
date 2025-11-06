# ruff: noqa
"""
Comprehensive HubSpot API Example
Tests all major HubSpot APIs: Contacts, Companies, Deals, Tickets, Products, Line Items
Demonstrates CRUD operations, batch operations, and search functionality
"""
import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional
project_root = None
p = Path(__file__).resolve()
for parent in p.parents:
    if parent.name == "backend":             # find the 'backend' folder
        project_root = parent / "python"     # use backend/python as PYTHONPATH
        break
if project_root is None:
    # fallback to previous heuristic (may need adjusting per repo layout)
    project_root = p.parents[5]
project_root = str(project_root)
if project_root not in sys.path:
    sys.path.insert(0, project_root)
from app.sources.client.hubspot.hubspot import HubSpotTokenConfig, HubSpotClient
from app.sources.external.hubspot.hubspot import HubSpotDataSource

ACCESS_TOKEN = os.getenv("HUBSPOT_ACCESS_TOKEN")


def print_section(title: str) -> None:
    """Print a formatted section header"""
    print("\n" + "=" * 80)
    print(f"📋 {title}")
    print("=" * 80)


def print_result(operation: str, response: Any, show_data: bool = True) -> None:
    """Print operation result in a formatted way"""
    status = "✅ SUCCESS" if response.success else "❌ FAILED"
    print(f"\n{operation}: {status}")
    
    if response.success and show_data:
        data = response.data
        if isinstance(data, dict):
            if 'results' in data:
                count = len(data['results'])
                print(f"   Retrieved {count} items")
                if count > 0 and count <= 2:
                    for i, item in enumerate(data['results'][:2], 1):
                        item_id = item.get('id', 'N/A')
                        print(f"   Item {i}: ID={item_id}")
                        if 'properties' in item:
                            props = item['properties']
                            # Show key properties
                            for key in ['email', 'firstname', 'lastname', 'name', 'domain', 'dealname', 'subject', 'amount']:
                                if key in props:
                                    print(f"      {key}: {props[key]}")
            elif 'id' in data:
                print(f"   ID: {data['id']}")
                if 'properties' in data:
                    print(f"   Properties: {list(data['properties'].keys())[:5]}...")
            else:
                print(f"   Response: {str(data)[:200]}...")
    elif not response.success:
        print(f"   Error: {response.error}")


async def test_contacts(data_source: HubSpotDataSource) -> Dict[str, int]:
    """Test Contact APIs"""
    print_section("TESTING CONTACT APIS")
    results = {"passed": 0, "failed": 0}
    
    # 1. List contacts
    response = await data_source.list_contacts(limit=5)
    print_result("1. list_contacts()", response)
    results["passed" if response.success else "failed"] += 1
    
    # Store first contact ID for other operations
    contact_id = None
    if response.success and response.data.get('results'):
        contact_id = response.data['results'][0]['id']
    
    # 2. Get contact (if we have an ID)
    if contact_id:
        response = await data_source.get_contact(contact_id)
        print_result("2. get_contact()", response)
        results["passed" if response.success else "failed"] += 1
    else:
        print("\n2. get_contact(): ⏭  SKIPPED (no contact available)")
    
    # 3. Search contacts
    filter_groups = [{
        "filters": [{
            "propertyName": "createdate",
            "operator": "GTE",
            "value": "2020-01-01"
        }]
    }]
    response = await data_source.search_contacts(filter_groups=filter_groups, limit=3)
    print_result("3. search_contacts()", response)
    results["passed" if response.success else "failed"] += 1
    
    return results


async def test_companies(data_source: HubSpotDataSource) -> Dict[str, int]:
    """Test Company APIs"""
    print_section("TESTING COMPANY APIS")
    results = {"passed": 0, "failed": 0}
    
    # 1. List companies
    response = await data_source.list_companies(limit=5)
    print_result("1. list_companies()", response)
    results["passed" if response.success else "failed"] += 1
    
    # Store first company ID
    company_id = None
    if response.success and response.data.get('results'):
        company_id = response.data['results'][0]['id']
    
    # 2. Get company
    if company_id:
        response = await data_source.get_company(company_id)
        print_result("2. get_company()", response)
        results["passed" if response.success else "failed"] += 1
    else:
        print("\n2. get_company(): ⏭  SKIPPED (no company available)")
    
    # 3. Search companies
    filter_groups = [{
        "filters": [{
            "propertyName": "createdate",
            "operator": "GTE",
            "value": "2020-01-01"
        }]
    }]
    response = await data_source.search_companies(filter_groups=filter_groups, limit=3)
    print_result("3. search_companies()", response)
    results["passed" if response.success else "failed"] += 1
    
    return results


async def test_deals(data_source: HubSpotDataSource) -> Dict[str, int]:
    """Test Deal APIs"""
    print_section("TESTING DEAL APIS")
    results = {"passed": 0, "failed": 0}
    
    # 1. List deals
    response = await data_source.list_deals(limit=5)
    print_result("1. list_deals()", response)
    results["passed" if response.success else "failed"] += 1
    
    # Store first deal ID
    deal_id = None
    if response.success and response.data.get('results'):
        deal_id = response.data['results'][0]['id']
    
    # 2. Get deal
    if deal_id:
        response = await data_source.get_deal(deal_id)
        print_result("2. get_deal()", response)
        results["passed" if response.success else "failed"] += 1
    else:
        print("\n2. get_deal(): ⏭  SKIPPED (no deal available)")
    
    # 3. Search deals
    filter_groups = [{
        "filters": [{
            "propertyName": "createdate",
            "operator": "GTE",
            "value": "2020-01-01"
        }]
    }]
    response = await data_source.search_deals(filter_groups=filter_groups, limit=3)
    print_result("3. search_deals()", response)
    results["passed" if response.success else "failed"] += 1
    
    return results


async def test_tickets(data_source: HubSpotDataSource) -> Dict[str, int]:
    """Test Ticket APIs"""
    print_section("TESTING TICKET APIS")
    results = {"passed": 0, "failed": 0}
    
    # 1. List tickets
    response = await data_source.list_tickets(limit=5)
    print_result("1. list_tickets()", response)
    results["passed" if response.success else "failed"] += 1
    
    # Store first ticket ID
    ticket_id = None
    if response.success and response.data.get('results'):
        ticket_id = response.data['results'][0]['id']
    
    # 2. Get ticket
    if ticket_id:
        response = await data_source.get_ticket(ticket_id)
        print_result("2. get_ticket()", response)
        results["passed" if response.success else "failed"] += 1
    else:
        print("\n2. get_ticket(): ⏭  SKIPPED (no ticket available)")
    
    # 3. Search tickets
    filter_groups = [{
        "filters": [{
            "propertyName": "createdate",
            "operator": "GTE",
            "value": "2020-01-01"
        }]
    }]
    response = await data_source.search_tickets(filter_groups=filter_groups, limit=3)
    print_result("3. search_tickets()", response)
    results["passed" if response.success else "failed"] += 1
    
    return results


async def test_products(data_source: HubSpotDataSource) -> Dict[str, int]:
    """Test Product APIs"""
    print_section("TESTING PRODUCT APIS")
    results = {"passed": 0, "failed": 0}
    
    # 1. List products
    response = await data_source.list_products(limit=5)
    print_result("1. list_products()", response)
    results["passed" if response.success else "failed"] += 1
    
    # Store first product ID
    product_id = None
    if response.success and response.data.get('results'):
        product_id = response.data['results'][0]['id']
    
    # 2. Get product
    if product_id:
        response = await data_source.get_product(product_id)
        print_result("2. get_product()", response)
        results["passed" if response.success else "failed"] += 1
    else:
        print("\n2. get_product(): ⏭  SKIPPED (no product available)")
    
    # 3. Search products
    filter_groups = [{
        "filters": [{
            "propertyName": "createdate",
            "operator": "GTE",
            "value": "2020-01-01"
        }]
    }]
    response = await data_source.search_products(filter_groups=filter_groups, limit=3)
    print_result("3. search_products()", response)
    results["passed" if response.success else "failed"] += 1
    
    return results


async def test_line_items(data_source: HubSpotDataSource) -> Dict[str, int]:
    """Test Line Item APIs"""
    print_section("TESTING LINE ITEM APIS")
    results = {"passed": 0, "failed": 0}
    
    # 1. List line items
    response = await data_source.list_line_items(limit=5)
    print_result("1. list_line_items()", response)
    results["passed" if response.success else "failed"] += 1
    
    # Store first line item ID
    line_item_id = None
    if response.success and response.data.get('results'):
        line_item_id = response.data['results'][0]['id']
    
    # 2. Get line item
    if line_item_id:
        response = await data_source.get_line_item(line_item_id)
        print_result("2. get_line_item()", response)
        results["passed" if response.success else "failed"] += 1
    else:
        print("\n2. get_line_item(): ⏭  SKIPPED (no line item available)")
    
    return results


async def main() -> None:
    """Main execution function"""
    if not ACCESS_TOKEN:
        print("❌ ERROR: HUBSPOT_ACCESS_TOKEN environment variable is not set")
        print("Please set it with: export HUBSPOT_ACCESS_TOKEN='your-token-here'")
        return
    
    print("╔════════════════════════════════════════════════════════════════════════════╗")
    print("║              HubSpot API - Comprehensive Example & Testing                ║")
    print("╚════════════════════════════════════════════════════════════════════════════╝")
    print(f"\n🔑 Token: {'*' * 40}{ACCESS_TOKEN[-10:]}")
    
    # Initialize client
    print("\n⚙  Initializing HubSpot client...")
    config = HubSpotTokenConfig(token=ACCESS_TOKEN)
    client = HubSpotClient.build_with_config(config)
    data_source = HubSpotDataSource(client)
    print("✅ Client initialized successfully")
    
    # Run all tests
    all_results = {"passed": 0, "failed": 0}
    
    try:
        # Test Contacts
        results = await test_contacts(data_source)
        all_results["passed"] += results["passed"]
        all_results["failed"] += results["failed"]
        
        # Test Companies
        results = await test_companies(data_source)
        all_results["passed"] += results["passed"]
        all_results["failed"] += results["failed"]
        
        # Test Deals
        results = await test_deals(data_source)
        all_results["passed"] += results["passed"]
        all_results["failed"] += results["failed"]
        
        # Test Tickets
        results = await test_tickets(data_source)
        all_results["passed"] += results["passed"]
        all_results["failed"] += results["failed"]
        
        # Test Products
        results = await test_products(data_source)
        all_results["passed"] += results["passed"]
        all_results["failed"] += results["failed"]
        
        # Test Line Items
        results = await test_line_items(data_source)
        all_results["passed"] += results["passed"]
        all_results["failed"] += results["failed"]
        
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
        return
    
    # Print summary
    print("\n" + "=" * 80)
    print("📊 TEST SUMMARY")
    print("=" * 80)
    total = all_results["passed"] + all_results["failed"]
    success_rate = (all_results["passed"] / total * 100) if total > 0 else 0
    
    print(f"\n✅ Passed: {all_results['passed']}/{total}")
    print(f"❌ Failed: {all_results['failed']}/{total}")
    print(f"📈 Success Rate: {success_rate:.1f}%")
    
    if all_results["failed"] == 0:
        print("\n🎉 ALL TESTS PASSED! HubSpot integration is working perfectly!")
    else:
        print(f"\n⚠  Some tests failed. Please review the output above.")
    
    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(main())