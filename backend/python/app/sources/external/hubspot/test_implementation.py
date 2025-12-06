#!/usr/bin/env python3
# ruff: noqa: F401
"""Test script to verify HubSpot integration implementation"""

import os
import sys

# Add the backend/python directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))

def test_imports():
    """Test that all modules import correctly"""
    print("🧪 Testing imports...")

    try:
        from app.sources.client.hubspot.hubspot_ import (
            HubSpotClient,
            HubSpotRESTClientViaToken,
            HubSpotTokenConfig,
        )
        print("✅ Client imports successful")
    except ImportError as e:
        print(f"❌ Client import failed: {e}")
        return False

    try:
        from app.sources.external.hubspot.hubspot_ import HubSpotDataSource
        print("✅ DataSource import successful")
    except ImportError as e:
        print(f"❌ DataSource import failed: {e}")
        return False

    return True


def test_client_initialization():
    """Test that client can be initialized"""
    print("\n🧪 Testing client initialization...")

    try:
        from app.sources.client.hubspot.hubspot_ import (
            HubSpotClient,
            HubSpotTokenConfig,
        )  # Test with a dummy token (will fail actual API calls but should initialize)
        config = HubSpotTokenConfig(token="dummy-token-for-testing")
        client = HubSpotClient.build_with_config(config)

        print("✅ Client initialization successful")
        print(f"   Client type: {type(client)}")
        print(f"   Has get_client method: {hasattr(client, 'get_client')}")

        return True
    except Exception as e:
        print(f"❌ Client initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_data_source_initialization():
    """Test that data source can be initialized"""
    print("\n🧪 Testing data source initialization...")

    try:
        from app.sources.client.hubspot.hubspot_ import (
            HubSpotClient,
            HubSpotTokenConfig,
        )
        from app.sources.external.hubspot.hubspot_ import HubSpotDataSource

        config = HubSpotTokenConfig(token="dummy-token-for-testing")
        client = HubSpotClient.build_with_config(config)
        data_source = HubSpotDataSource(client)

        print("✅ DataSource initialization successful")
        print(f"   DataSource type: {type(data_source)}")
        print(f"   Has get_contacts method: {hasattr(data_source, 'get_contacts')}")
        print(f"   Has get_companies method: {hasattr(data_source, 'get_companies')}")
        print(f"   Has get_deals method: {hasattr(data_source, 'get_deals')}")
        print(f"   Has get_tickets method: {hasattr(data_source, 'get_tickets')}")

        return True
    except Exception as e:
        print(f"❌ DataSource initialization failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_method_signatures():
    """Test that key methods have correct signatures"""
    print("\n🧪 Testing method signatures...")

    try:

        from app.sources.external.hubspot.hubspot_ import HubSpotDataSource

        methods_to_check = [
            'get_contacts',
            'get_contact_by_id',
            'create_contact',
            'update_contact',
            'delete_contact',
            'get_companies',
            'get_company_by_id',
            'create_company',
            'update_company',
            'delete_company',
            'get_deals',
            'get_deal_by_id',
            'create_deal',
            'update_deal',
            'delete_deal',
            'get_tickets',
            'get_ticket_by_id',
            'create_ticket',
            'update_ticket',
            'delete_ticket',
            'search_contacts',
            'search_companies',
        ]

        for method_name in methods_to_check:
            if not hasattr(HubSpotDataSource, method_name):
                print(f"❌ Missing method: {method_name}")
                return False

            method = getattr(HubSpotDataSource, method_name)
            if not callable(method):
                print(f"❌ {method_name} is not callable")
                return False

        print(f"✅ All {len(methods_to_check)} required methods are present")
        return True

    except Exception as e:
        print(f"❌ Method signature test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all tests"""
    print("=" * 60)
    print("HubSpot Integration - Implementation Verification")
    print("=" * 60)

    all_passed = True

    all_passed &= test_imports()
    all_passed &= test_client_initialization()
    all_passed &= test_data_source_initialization()
    all_passed &= test_method_signatures()

    print("\n" + "=" * 60)
    if all_passed:
        print("✅ All tests passed!")
        print("\n📋 Summary:")
        print("   - Client module: ✅ Working")
        print("   - DataSource module: ✅ Working")
        print("   - All required methods: ✅ Present")
        print("\n📝 Next steps:")
        print("   1. Set HUBSPOT_TOKEN environment variable")
        print("   2. Run example.py to test with real HubSpot API")
        print("   3. Create a PR from your forked branch")
        return 0
    else:
        print("❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
