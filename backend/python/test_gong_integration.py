#!/usr/bin/env python3
"""Test script for Gong integration

This script tests the basic functionality of the Gong integration
without requiring actual API credentials.
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

# Add the app directory to Python path
sys.path.insert(0, "app")

from app.sources.client.gong.gong import GongClient, create_gong_client
from app.sources.external.gong.gong import GongService, create_gong_service


async def test_gong_client_creation():
    """Test that GongClient can be created."""
    print("🧪 Testing GongClient creation...")

    try:
        client = GongClient("test_key", "test_secret")
        assert client.access_key == "test_key"
        assert client.access_key_secret == "test_secret"
        assert client.BASE_URL == "https://api.gong.io/v2/"
        print("✅ GongClient creation: PASSED")
        return True
    except Exception as e:
        print(f"❌ GongClient creation: FAILED - {e}")
        return False


async def test_gong_service_creation():
    """Test that GongService can be created."""
    print("🧪 Testing GongService creation...")

    try:
        client = GongClient("test_key", "test_secret")
        service = GongService(client)
        assert service.client == client
        print("✅ GongService creation: PASSED")
        return True
    except Exception as e:
        print(f"❌ GongService creation: FAILED - {e}")
        return False


async def test_factory_functions():
    """Test factory functions."""
    print("🧪 Testing factory functions...")

    try:
        # Test client factory
        client = await create_gong_client("test_key", "test_secret")
        assert isinstance(client, GongClient)

        # Test service factory
        service = await create_gong_service("test_key", "test_secret")
        assert isinstance(service, GongService)

        print("✅ Factory functions: PASSED")
        return True
    except Exception as e:
        print(f"❌ Factory functions: FAILED - {e}")
        return False


async def test_client_methods_exist():
    """Test that all expected client methods exist."""
    print("🧪 Testing client methods exist...")

    try:
        client = GongClient("test_key", "test_secret")

        # Check that all expected methods exist
        expected_methods = [
            "test_connection", "get_users", "get_calls", "get_call_details",
            "get_call_transcript", "get_workspaces", "get_deals", "get_meetings",
            "get_all_users", "get_all_calls",
        ]

        for method_name in expected_methods:
            assert hasattr(client, method_name), f"Method {method_name} not found"
            assert callable(getattr(client, method_name)), f"Method {method_name} is not callable"

        print("✅ Client methods exist: PASSED")
        return True
    except Exception as e:
        print(f"❌ Client methods exist: FAILED - {e}")
        return False


async def test_service_methods_exist():
    """Test that all expected service methods exist."""
    print("🧪 Testing service methods exist...")

    try:
        client = GongClient("test_key", "test_secret")
        service = GongService(client)

        # Check that all expected methods exist
        expected_methods = [
            "validate_connection", "get_workspace_info", "get_recent_calls",
            "get_call_with_transcript", "get_user_activity_summary",
            "search_calls_by_keywords", "get_team_performance_metrics",
            "export_calls_to_dict",
        ]

        for method_name in expected_methods:
            assert hasattr(service, method_name), f"Method {method_name} not found"
            assert callable(getattr(service, method_name)), f"Method {method_name} is not callable"

        print("✅ Service methods exist: PASSED")
        return True
    except Exception as e:
        print(f"❌ Service methods exist: FAILED - {e}")
        return False


async def test_mock_api_call():
    """Test API call with mocked response."""
    print("🧪 Testing mock API call...")

    try:
        client = GongClient("test_key", "test_secret")

        # Mock the _make_request method
        mock_response = {"users": [{"id": "123", "firstName": "Test", "lastName": "User"}]}
        client._make_request = AsyncMock(return_value=mock_response)

        # Test the get_users method
        result = await client.get_users()
        assert result == mock_response

        print("✅ Mock API call: PASSED")
        return True
    except Exception as e:
        print(f"❌ Mock API call: FAILED - {e}")
        return False


async def test_service_with_mock_client():
    """Test service with mocked client."""
    print("🧪 Testing service with mock client...")

    try:
        # Create a mock client
        mock_client = MagicMock()
        mock_client.test_connection = AsyncMock(return_value=True)
        mock_client.get_workspaces = AsyncMock(return_value={"workspaces": []})
        mock_client.get_users = AsyncMock(return_value={"records": {"totalRecords": 0}})

        service = GongService(mock_client)

        # Test validate_connection
        is_valid, error = await service.validate_connection()
        assert is_valid is True
        assert error is None

        # Test get_workspace_info
        workspace_info = await service.get_workspace_info()
        assert "workspaces" in workspace_info
        assert workspace_info["total_workspaces"] == 0

        print("✅ Service with mock client: PASSED")
        return True
    except Exception as e:
        print(f"❌ Service with mock client: FAILED - {e}")
        return False


async def run_all_tests():
    """Run all tests."""
    print("🚀 Running Gong Integration Tests")
    print("=" * 50)

    tests = [
        test_gong_client_creation,
        test_gong_service_creation,
        test_factory_functions,
        test_client_methods_exist,
        test_service_methods_exist,
        test_mock_api_call,
        test_service_with_mock_client,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            result = await test()
            if result:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ {test.__name__}: FAILED - {e}")
            failed += 1

        print("-" * 30)

    print("\n📊 Test Results:")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    print(f"📈 Success Rate: {(passed / (passed + failed)) * 100:.1f}%")

    if failed == 0:
        print("\n🎉 All tests passed! Gong integration is ready.")
        return True
    print(f"\n⚠️  {failed} test(s) failed. Please check the errors above.")
    return False


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
