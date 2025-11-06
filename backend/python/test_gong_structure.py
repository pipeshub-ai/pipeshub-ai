#!/usr/bin/env python3
"""
Test script to verify Gong integration structure and basic functionality
"""

import sys
import importlib.util
from unittest.mock import MagicMock

def load_module_from_path(module_name, file_path):
    """Load a module from a file path"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module {module_name} from {file_path}")
    
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_gong_client_structure():
    """Test GongClient structure and methods"""
    print("🧪 Testing GongClient structure...")
    
    try:
        # Mock dependencies to avoid import issues
        sys.modules['app.config.configuration_service'] = MagicMock()
        sys.modules['app.sources.client.http.http_client'] = MagicMock()
        sys.modules['app.sources.client.iclient'] = MagicMock()
        
        # Load the module
        gong_client_module = load_module_from_path(
            "gong_client", 
            "app/sources/client/gong/gong.py"
        )
        
        # Test that required classes exist
        assert hasattr(gong_client_module, 'GongRESTClientViaApiKey'), "GongRESTClientViaApiKey class missing"
        assert hasattr(gong_client_module, 'GongApiKeyConfig'), "GongApiKeyConfig class missing"
        assert hasattr(gong_client_module, 'GongClient'), "GongClient class missing"
        
        # Test GongApiKeyConfig
        config_class = gong_client_module.GongApiKeyConfig
        config = config_class(access_key="test_key", access_key_secret="test_secret")
        assert config.access_key == "test_key"
        assert config.access_key_secret == "test_secret"
        assert config.ssl is True  # Default value
        
        print("✅ GongClient structure: PASSED")
        return True
        
    except Exception as e:
        print(f"❌ GongClient structure: FAILED - {e}")
        return False

def test_gong_data_source_structure():
    """Test GongDataSource structure and methods"""
    print("🧪 Testing GongDataSource structure...")
    
    try:
        # Mock dependencies
        sys.modules['app.sources.client.gong.gong'] = MagicMock()
        sys.modules['app.sources.client.http.http_request'] = MagicMock()
        sys.modules['app.sources.client.http.http_response'] = MagicMock()
        
        # Load the module
        gong_data_source_module = load_module_from_path(
            "gong_data_source", 
            "app/sources/external/gong/gong.py"
        )
        
        # Test that required classes and functions exist
        assert hasattr(gong_data_source_module, 'GongDataSource'), "GongDataSource class missing"
        assert hasattr(gong_data_source_module, '_safe_format_url'), "_safe_format_url function missing"
        assert hasattr(gong_data_source_module, '_as_str_dict'), "_as_str_dict function missing"
        
        # Test utility functions
        _safe_format_url = gong_data_source_module._safe_format_url
        _as_str_dict = gong_data_source_module._as_str_dict
        
        # Test _safe_format_url
        result = _safe_format_url("/users/{user_id}", {"user_id": "123"})
        assert result == "/users/123"
        
        # Test _as_str_dict
        result = _as_str_dict({"key1": 123, "key2": True, "key3": "test"})
        assert result == {"key1": "123", "key2": "True", "key3": "test"}
        
        # Test GongDataSource class exists and has expected methods
        data_source_class = gong_data_source_module.GongDataSource
        expected_methods = [
            'get_users', 'get_calls', 'get_call_details', 'get_call_transcript',
            'get_workspaces', 'get_deals', 'get_meetings', 'get_crm_objects',
            'get_stats_activity', 'get_library_calls'
        ]
        
        for method_name in expected_methods:
            assert hasattr(data_source_class, method_name), f"Method {method_name} missing"
        
        print("✅ GongDataSource structure: PASSED")
        return True
        
    except Exception as e:
        print(f"❌ GongDataSource structure: FAILED - {e}")
        return False

def test_example_structure():
    """Test example.py structure"""
    print("🧪 Testing example.py structure...")
    
    try:
        # Mock dependencies
        sys.modules['app.sources.client.gong.gong'] = MagicMock()
        sys.modules['app.sources.client.http.http_response'] = MagicMock()
        sys.modules['app.sources.external.gong.gong'] = MagicMock()
        
        # Load the module
        example_module = load_module_from_path(
            "gong_example", 
            "app/sources/external/gong/example.py"
        )
        
        # Test that required functions exist
        assert hasattr(example_module, 'main'), "main function missing"
        assert hasattr(example_module, 'test_gong_apis'), "test_gong_apis function missing"
        
        print("✅ Example structure: PASSED")
        return True
        
    except Exception as e:
        print(f"❌ Example structure: FAILED - {e}")
        return False

def main():
    """Run all structure tests"""
    print("🚀 Testing Gong Integration Structure")
    print("=" * 50)
    
    tests = [
        test_gong_client_structure,
        test_gong_data_source_structure,
        test_example_structure
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            result = test()
            if result:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"❌ {test.__name__}: FAILED - {e}")
            failed += 1
        
        print("-" * 30)
    
    print(f"\n📊 Test Results:")
    print(f"✅ Passed: {passed}")
    print(f"❌ Failed: {failed}")
    print(f"📈 Success Rate: {(passed / (passed + failed)) * 100:.1f}%")
    
    if failed == 0:
        print("\n🎉 All structure tests passed!")
        print("✅ Gong integration follows the correct patterns!")
        return True
    else:
        print(f"\n⚠️  {failed} test(s) failed. Please check the errors above.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)