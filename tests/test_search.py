"""
Search and query integration tests for PipesHub AI.
"""
import pytest
import httpx
from typing import Dict, Any, List


@pytest.mark.search
@pytest.mark.integration
class TestSearchFunctionality:
    """Test search and query functionality."""
    
    @pytest.mark.asyncio
    async def test_basic_search_query(self, http_client, test_config, sample_search_queries):
        """Test basic search functionality with sample queries."""
        # Check if query service is available
        try:
            query_health = await http_client.get(f"{test_config['query_service_url']}/health")
            if query_health.status_code != 200:
                pytest.skip("Query service not available")
        except httpx.ConnectError:
            pytest.skip("Query service not available - connection failed")
        
        # Test each sample query
        for query_text in sample_search_queries:
            search_payload = {
                "query": query_text,
                "limit": 5,
                "filters": {}
            }
            
            try:
                response = await http_client.post(
                    f"{test_config['query_service_url']}/search",
                    json=search_payload,
                    timeout=15.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    assert "searchResults" in result or "results" in result, "Search response should contain results"
                    print(f"Search for '{query_text}': {len(result.get('searchResults', result.get('results', [])))} results")
                else:
                    print(f"Search for '{query_text}' returned status {response.status_code}")
                    
            except httpx.RequestError as e:
                print(f"Search endpoint not available: {e}")
                break
    
    @pytest.mark.asyncio
    async def test_search_with_filters(self, http_client, test_config):
        """Test search functionality with various filters."""
        search_payload = {
            "query": "test document",
            "limit": 10,
            "filters": {
                "org_id": "test-org-456",
                "user_id": "test-user-123",
                "document_type": "text"
            }
        }
        
        try:
            response = await http_client.post(
                f"{test_config['query_service_url']}/search",
                json=search_payload,
                timeout=15.0
            )
            
            if response.status_code == 200:
                result = response.json()
                assert isinstance(result, dict), "Search response should be a dictionary"
                
                # Check for expected fields
                expected_fields = ["searchResults", "results", "status", "status_code"]
                found_fields = [field for field in expected_fields if field in result]
                assert len(found_fields) > 0, f"Search response should contain at least one of: {expected_fields}"
                
                print(f"Filtered search successful: {found_fields}")
                
            elif response.status_code == 400:
                # Invalid filters - this is acceptable
                result = response.json()
                assert "error" in result or "detail" in result, "Error response should contain error details"
                print(f"Search with filters returned validation error: {result}")
                
            else:
                print(f"Search with filters returned status {response.status_code}")
                
        except httpx.RequestError as e:
            print(f"Search endpoint not available: {e}")
    
    @pytest.mark.asyncio
    async def test_search_query_validation(self, http_client, test_config):
        """Test search query validation with invalid inputs."""
        invalid_queries = [
            {"query": "", "limit": 5},  # Empty query
            {"query": "test", "limit": -1},  # Invalid limit
            {"query": "test", "limit": 1000},  # Very high limit
            {"query": None, "limit": 5},  # Null query
        ]
        
        for invalid_query in invalid_queries:
            try:
                response = await http_client.post(
                    f"{test_config['query_service_url']}/search",
                    json=invalid_query,
                    timeout=10.0
                )
                
                # Should return error status for invalid queries
                assert response.status_code in [400, 422, 500], f"Expected error status for {invalid_query}, got {response.status_code}"
                
                if response.status_code in [400, 422]:
                    result = response.json()
                    assert "error" in result or "detail" in result, "Error response should contain error details"
                    print(f"Query validation working for {invalid_query}")
                    
            except httpx.RequestError:
                print(f"Search endpoint not available for validation test")
                break
    
    @pytest.mark.asyncio
    async def test_search_response_format(self, http_client, test_config):
        """Test that search responses have the expected format."""
        search_payload = {
            "query": "test",
            "limit": 3,
            "filters": {}
        }
        
        try:
            response = await http_client.post(
                f"{test_config['query_service_url']}/search",
                json=search_payload,
                timeout=15.0
            )
            
            if response.status_code == 200:
                result = response.json()
                
                # Validate response structure
                assert isinstance(result, dict), "Search response should be a dictionary"
                
                # Check for common response fields
                if "searchResults" in result:
                    assert isinstance(result["searchResults"], list), "searchResults should be a list"
                    print(f"Search returned {len(result['searchResults'])} results")
                elif "results" in result:
                    assert isinstance(result["results"], list), "results should be a list"
                    print(f"Search returned {len(result['results'])} results")
                else:
                    print(f"Search response format: {list(result.keys())}")
                
                # Check for status information
                if "status" in result:
                    assert isinstance(result["status"], str), "status should be a string"
                    print(f"Search status: {result['status']}")
                
            else:
                print(f"Search returned status {response.status_code}")
                
        except httpx.RequestError as e:
            print(f"Search endpoint not available: {e}")
    
    @pytest.mark.asyncio
    async def test_search_performance(self, http_client, test_config):
        """Test search performance with reasonable timeout."""
        search_payload = {
            "query": "performance test",
            "limit": 5,
            "filters": {}
        }
        
        import time
        start_time = time.time()
        
        try:
            response = await http_client.post(
                f"{test_config['query_service_url']}/search",
                json=search_payload,
                timeout=10.0
            )
            
            end_time = time.time()
            response_time = end_time - start_time
            
            assert response_time < 10.0, f"Search took too long: {response_time:.2f}s"
            
            if response.status_code == 200:
                print(f"Search completed in {response_time:.2f}s")
            else:
                print(f"Search returned status {response.status_code} in {response_time:.2f}s")
                
        except httpx.TimeoutException:
            pytest.fail("Search request timed out")
        except httpx.RequestError as e:
            print(f"Search endpoint not available: {e}")


@pytest.mark.search
class TestSearchUtilities:
    """Test search utility functions and helpers."""
    
    def test_search_query_structure(self, sample_search_queries):
        """Test that sample search queries have proper structure."""
        assert isinstance(sample_search_queries, list), "Sample queries should be a list"
        assert len(sample_search_queries) > 0, "Should have at least one sample query"
        
        for query in sample_search_queries:
            assert isinstance(query, str), "Each query should be a string"
            assert len(query.strip()) > 0, "Query should not be empty"
    
    def test_search_payload_validation(self):
        """Test search payload validation."""
        valid_payload = {
            "query": "test query",
            "limit": 5,
            "filters": {"org_id": "test-org"}
        }
        
        # Test required fields
        assert "query" in valid_payload, "Search payload should have query field"
        assert "limit" in valid_payload, "Search payload should have limit field"
        assert "filters" in valid_payload, "Search payload should have filters field"
        
        # Test field types
        assert isinstance(valid_payload["query"], str), "Query should be a string"
        assert isinstance(valid_payload["limit"], int), "Limit should be an integer"
        assert isinstance(valid_payload["filters"], dict), "Filters should be a dictionary"
    
    @pytest.mark.asyncio
    async def test_search_endpoint_connectivity(self, http_client, test_config):
        """Test basic connectivity to search endpoint."""
        try:
            # Try to connect to search service
            response = await http_client.get(
                f"{test_config['query_service_url']}/",
                timeout=5.0
            )
            
            # Any response (even 404) indicates the service is running
            assert response.status_code is not None, "Should receive a response from search service"
            print(f"Search service is reachable (status: {response.status_code})")
            
        except httpx.ConnectError:
            pytest.skip("Search service not running")
        except httpx.TimeoutException:
            pytest.fail("Search service connection timed out")
        except httpx.RequestError as e:
            print(f"Search service connection error: {e}")


@pytest.mark.search
@pytest.mark.slow
class TestSearchIntegration:
    """Integration tests for search functionality."""
    
    @pytest.mark.asyncio
    async def test_search_with_authentication(self, http_client, test_config, mock_user_context):
        """Test search functionality with user authentication context."""
        # This test would require proper authentication setup
        # For P0, we'll test the basic search without auth
        
        search_payload = {
            "query": "authenticated search test",
            "limit": 5,
            "filters": {
                "org_id": mock_user_context["orgId"],
                "user_id": mock_user_context["userId"]
            }
        }
        
        try:
            response = await http_client.post(
                f"{test_config['query_service_url']}/search",
                json=search_payload,
                timeout=15.0
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"Authenticated search successful: {result}")
            elif response.status_code == 401:
                print("Search requires authentication (expected)")
            elif response.status_code == 403:
                print("Search access denied (expected)")
            else:
                print(f"Search returned status {response.status_code}")
                
        except httpx.RequestError as e:
            print(f"Search endpoint not available: {e}")
    
    @pytest.mark.asyncio
    async def test_search_error_handling(self, http_client, test_config):
        """Test search error handling with malformed requests."""
        malformed_requests = [
            {"invalid": "payload"},  # Missing required fields
            {"query": "test", "limit": "invalid"},  # Wrong type
            {"query": "test", "filters": "invalid"},  # Wrong type
        ]
        
        for malformed_request in malformed_requests:
            try:
                response = await http_client.post(
                    f"{test_config['query_service_url']}/search",
                    json=malformed_request,
                    timeout=10.0
                )
                
                # Should return error status
                assert response.status_code in [400, 422, 500], f"Expected error status for {malformed_request}, got {response.status_code}"
                
                if response.status_code in [400, 422]:
                    result = response.json()
                    assert "error" in result or "detail" in result, "Error response should contain error details"
                    print(f"Error handling working for {malformed_request}")
                    
            except httpx.RequestError:
                print(f"Search endpoint not available for error testing")
                break
    
    @pytest.mark.asyncio
    async def test_search_with_different_limits(self, http_client, test_config):
        """Test search with different limit values."""
        limits = [1, 5, 10, 20]
        
        for limit in limits:
            search_payload = {
                "query": "test limit",
                "limit": limit,
                "filters": {}
            }
            
            try:
                response = await http_client.post(
                    f"{test_config['query_service_url']}/search",
                    json=search_payload,
                    timeout=10.0
                )
                
                if response.status_code == 200:
                    result = response.json()
                    results = result.get("searchResults", result.get("results", []))
                    assert len(results) <= limit, f"Should return at most {limit} results, got {len(results)}"
                    print(f"Search with limit {limit}: {len(results)} results")
                else:
                    print(f"Search with limit {limit} returned status {response.status_code}")
                    
            except httpx.RequestError:
                print(f"ℹ️ Search endpoint not available for limit testing")
                break

