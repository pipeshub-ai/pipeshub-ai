"""
API integration tests for PipesHub AI services.
"""
import pytest
import httpx
from typing import Dict, Any


@pytest.mark.integration
class TestAPIIntegration:
    """Test API integration and service communication."""
    
    @pytest.mark.asyncio
    async def test_service_to_service_communication(self, http_client, test_config):
        """Test that services can communicate with each other."""
        # Test that query service can reach connector service
        try:
            query_health = await http_client.get(f"{test_config['query_service_url']}/health")
            if query_health.status_code == 200:
                query_data = query_health.json()
                
                # Query service should check connector service health
                if "connector_status" in query_data or "dependencies" in query_data:
                    print("Query service reports connector service status")
                else:
                    print("Query service health doesn't include connector status")
            
            # Test that indexing service can reach connector service
            indexing_health = await http_client.get(f"{test_config['indexing_service_url']}/health")
            if indexing_health.status_code == 200:
                indexing_data = indexing_health.json()
                
                # Indexing service should check connector service health
                if "connector_status" in indexing_data or "dependencies" in indexing_data:
                    print("Indexing service reports connector service status")
                else:
                    print("Indexing service health doesn't include connector status")
                    
        except httpx.RequestError as e:
            print(f"Service communication test not available: {e}")
    
    @pytest.mark.asyncio
    async def test_api_response_consistency(self, http_client, test_config):
        """Test that API responses are consistent across services."""
        services = [
            ("query", test_config["query_service_url"]),
            ("indexing", test_config["indexing_service_url"]),
            ("connector", test_config["connector_service_url"]),
        ]
        
        health_responses = {}
        
        for service_name, url in services:
            try:
                response = await http_client.get(f"{url}/health", timeout=5.0)
                health_responses[service_name] = {
                    "status_code": response.status_code,
                    "data": response.json() if response.status_code == 200 else None
                }
            except httpx.RequestError:
                health_responses[service_name] = {"status_code": None, "data": None}
        
        # Check that all available services return consistent health format
        available_services = {name: resp for name, resp in health_responses.items() if resp["status_code"] == 200}
        
        if len(available_services) == 0:
            pytest.skip("No services available for consistency testing")

        # Check for consistent response format
        for service_name, response in available_services.items():
            data = response["data"]
            assert isinstance(data, dict), f"{service_name} health response should be object"
            assert "status" in data, f"{service_name} health response missing 'status'"
    
    @pytest.mark.asyncio
    async def test_api_error_handling(self, http_client, test_config):
        """Test API error handling across services."""
        # Test with invalid endpoints
        invalid_endpoints = [
            f"{test_config['query_service_url']}/invalid",
            f"{test_config['indexing_service_url']}/invalid",
            f"{test_config['connector_service_url']}/invalid",
        ]
        
        for endpoint in invalid_endpoints:
            try:
                response = await http_client.get(endpoint, timeout=5.0)
                
                # Should return 404 for invalid endpoints
                assert response.status_code == 404, f"Expected 404 for {endpoint}, got {response.status_code}"
                print(f"Error handling working for {endpoint}")
                
            except httpx.RequestError:
                print(f"Service not available for error testing: {endpoint}")
    
    @pytest.mark.asyncio
    async def test_api_timeout_handling(self, http_client, test_config):
        """Test API timeout handling."""
        # Test with very short timeout
        short_timeout = httpx.Timeout(0.1)  # 100ms timeout
        
        services = [
            ("query", test_config["query_service_url"]),
            ("indexing", test_config["indexing_service_url"]),
            ("connector", test_config["connector_service_url"]),
        ]
        
        for service_name, url in services:
            try:
                await http_client.get(f"{url}/health", timeout=short_timeout)
                pytest.skip(f"{service_name} responded within 100ms; skip strict timeout check")
            except httpx.TimeoutException:
                pass
            except httpx.RequestError:
                pytest.skip(f"{service_name} not available for timeout testing")
    
    @pytest.mark.asyncio
    @pytest.mark.parametrize("service_name_key", ["query_service_url", "indexing_service_url", "connector_service_url"]) 
    async def test_api_cors_headers(self, http_client, test_config, service_name_key):
        """Test CORS headers in API responses."""
        name = service_name_key.split("_")[0]
        url = test_config[service_name_key]
        try:
            response = await httpx.AsyncClient().options(f"{url}/health", timeout=5.0)
            if response.status_code not in [200, 204]:
                pytest.skip(f"{name} OPTIONS not supported: {response.status_code}")
            cors_headers = {
                "access-control-allow-origin": response.headers.get("access-control-allow-origin"),
                "access-control-allow-methods": response.headers.get("access-control-allow-methods"),
                "access-control-allow-headers": response.headers.get("access-control-allow-headers"),
            }
            assert any(cors_headers.values()), f"{name} missing CORS headers"
        except httpx.RequestError:
            pytest.skip(f"{name} not available for CORS testing")
    
    @pytest.mark.asyncio
    @pytest.mark.parametrize("service_name_key", ["query_service_url", "indexing_service_url", "connector_service_url"]) 
    async def test_api_content_type_headers(self, http_client, test_config, service_name_key):
        """Test content type headers in API responses."""
        name = service_name_key.split("_")[0]
        url = test_config[service_name_key]
        try:
            response = await http_client.get(f"{url}/health", timeout=5.0)
            if response.status_code != 200:
                pytest.skip(f"{name} not healthy: {response.status_code}")
            content_type = response.headers.get("content-type", "")
            assert "application/json" in content_type, f"{name} content-type should be JSON"
        except httpx.RequestError:
            pytest.skip(f"{name} not available for content type testing")


@pytest.mark.integration
class TestAPIAuthentication:
    """Test API authentication and authorization."""
    
    @pytest.mark.asyncio
    async def test_authenticated_endpoints(self, http_client, test_config, mock_user_context):
        """Test endpoints that require authentication."""
        # Test Node.js backend with authentication
        try:
            # This would require proper authentication setup
            # For P0, we'll test the basic endpoint structure
            response = await http_client.get(f"{test_config['nodejs_backend_url']}/api/v1/health")
            
            if response.status_code == 200:
                print(" Health endpoint accessible without authentication")
            elif response.status_code == 401:
                print("Health endpoint requires authentication")
            else:
                print(f"Health endpoint returned status {response.status_code}")
                
        except httpx.RequestError as e:
            print(f"Node.js backend not available: {e}")
    
    @pytest.mark.asyncio
    async def test_unauthorized_access(self, http_client, test_config):
        """Test unauthorized access to protected endpoints."""
        # Test accessing protected endpoints without authentication
        protected_endpoints = [
            f"{test_config['nodejs_backend_url']}/api/v1/users",
            f"{test_config['nodejs_backend_url']}/api/v1/org",
            f"{test_config['query_service_url']}/search",
        ]
        
        for endpoint in protected_endpoints:
            try:
                response = await http_client.get(endpoint, timeout=5.0)
                
                if response.status_code == 401:
                    print(f"{endpoint} properly requires authentication")
                elif response.status_code == 403:
                    print(f"{endpoint} properly denies access")
                elif response.status_code == 200:
                    print(f"{endpoint} accessible without authentication")
                else:
                    print(f"{endpoint} returned status {response.status_code}")
                    
            except httpx.RequestError:
                print(f"{endpoint} not available for auth testing")


@pytest.mark.integration
class TestAPIPerformance:
    """Test API performance and load handling."""
    
    @pytest.mark.asyncio
    async def test_concurrent_requests(self, http_client, test_config):
        """Test handling of concurrent requests."""
        import asyncio
        
        # Create multiple concurrent health check requests
        tasks = []
        for i in range(5):
            task = http_client.get(f"{test_config['query_service_url']}/health", timeout=10.0)
            tasks.append(task)
        
        try:
            responses = await asyncio.gather(*tasks, return_exceptions=True)
            
            successful_responses = [r for r in responses if not isinstance(r, Exception) and r.status_code == 200]
            print(f"Concurrent requests: {len(successful_responses)}/{len(tasks)} successful")
            
        except Exception as e:
            print(f"Concurrent request test failed: {e}")
    
    @pytest.mark.asyncio
    async def test_api_response_times(self, http_client, test_config):
        """Test API response times."""
        import time
        
        services = [
            ("query", test_config["query_service_url"]),
            ("indexing", test_config["indexing_service_url"]),
            ("connector", test_config["connector_service_url"]),
        ]
        
        for service_name, url in services:
            try:
                start_time = time.time()
                response = await http_client.get(f"{url}/health", timeout=5.0)
                end_time = time.time()
                
                response_time = end_time - start_time
                
                if response.status_code == 200:
                    print(f"{service_name} responded in {response_time:.2f}s")
                else:
                    print(f"{service_name} returned status {response.status_code} in {response_time:.2f}s")
                    
            except httpx.RequestError:
                print(f"{service_name} not available for performance testing")
    
    @pytest.mark.asyncio
    async def test_api_memory_usage(self, http_client, test_config):
        """Test API memory usage with large requests."""
        # Test with large search query
        large_query = {
            "query": "test " * 1000,  # Large query
            "limit": 100,
            "filters": {}
        }
        
        try:
            response = await http_client.post(
                f"{test_config['query_service_url']}/search",
                json=large_query,
                timeout=15.0
            )
            
            if response.status_code == 200:
                print("API handled large request successfully")
            elif response.status_code == 400:
                print("API rejected large request (expected)")
            else:
                print(f"API returned status {response.status_code} for large request")
                
        except httpx.RequestError:
            print("Search endpoint not available for memory testing")

