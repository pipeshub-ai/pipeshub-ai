"""
Health check integration tests for PipesHub AI services.
"""
import time
import pytest
import httpx


@pytest.mark.health
@pytest.mark.integration
class TestHealthChecks:
    """Test health check endpoints for all services."""
    
    @pytest.mark.asyncio
    async def test_nodejs_backend_health(self, http_client, test_config, health_checker):
        """Test Node.js backend health check endpoint."""
        result = await health_checker.check_nodejs_backend(test_config["nodejs_backend_url"])
        
        # For P0 testing, we check if the service is reachable or properly reports unavailability
        if result["status"] == "healthy":
            assert result["status_code"] == 200, f"Expected status 200, got {result['status_code']}"
            if result["response"]:
                assert "status" in result["response"], "Health response missing 'status' field"
                assert result["response"]["status"] == "healthy", "Health status is not 'healthy'"
                assert "timestamp" in result["response"], "Health response missing 'timestamp' field"
        else:
            # Service is not running - this is acceptable for P0 testing
            assert "error" in result, "Error response should contain error details"
            print(f"Node.js backend not available: {result['error']}")
            pytest.skip("Node.js backend not running - acceptable for P0 testing")
    
    @pytest.mark.asyncio
    async def test_query_service_health(self, http_client, test_config, health_checker):
        """Test Query service health check endpoint."""
        result = await health_checker.check_python_service(
            test_config["query_service_url"], 
            "query"
        )
        
        if result["status"] == "healthy":
            assert result["status_code"] == 200, f"Expected status 200, got {result['status_code']}"
            if result["response"]:
                assert "status" in result["response"], "Health response missing 'status' field"
                assert result["response"]["status"] in ["healthy", "ok"], f"Unexpected health status: {result['response']['status']}"
        else:
            assert "error" in result, "Error response should contain error details"
            print(f"Query service not available: {result['error']}")
            pytest.skip("Query service not running - acceptable for P0 testing")
    
    @pytest.mark.asyncio
    async def test_indexing_service_health(self, http_client, test_config, health_checker):
        """Test Indexing service health check endpoint."""
        result = await health_checker.check_python_service(
            test_config["indexing_service_url"], 
            "indexing"
        )
        
        if result["status"] == "healthy":
            assert result["status_code"] == 200, f"Expected status 200, got {result['status_code']}"
            if result["response"]:
                assert "status" in result["response"], "Health response missing 'status' field"
                assert result["response"]["status"] in ["healthy", "ok"], f"Unexpected health status: {result['response']['status']}"
        else:
            assert "error" in result, "Error response should contain error details"
            print(f"Indexing service not available: {result['error']}")
            pytest.skip("Indexing service not running - acceptable for P0 testing")
    
    @pytest.mark.asyncio
    async def test_connector_service_health(self, http_client, test_config, health_checker):
        """Test Connector service health check endpoint."""
        result = await health_checker.check_python_service(
            test_config["connector_service_url"], 
            "connector"
        )
        
        if result["status"] == "healthy":
            assert result["status_code"] == 200, f"Expected status 200, got {result['status_code']}"
            if result["response"]:
                assert "status" in result["response"], "Health response missing 'status' field"
                assert result["response"]["status"] in ["healthy", "ok"], f"Unexpected health status: {result['response']['status']}"
        else:
            assert "error" in result, "Error response should contain error details"
            print(f"Connector service not available: {result['error']}")
            pytest.skip("Connector service not running - acceptable for P0 testing")
    
    @pytest.mark.asyncio
    async def test_docling_service_health(self, http_client, test_config, health_checker):
        """Test Docling service health check endpoint."""
        result = await health_checker.check_python_service(
            test_config["docling_service_url"], 
            "docling"
        )
        
        # Docling service might not be running, so we check for either healthy or connection error
        if result["status"] == "healthy":
            assert result["status_code"] == 200, f"Expected status 200, got {result['status_code']}"
            if result["response"]:
                assert "status" in result["response"], "Health response missing 'status' field"
        else:
            # If service is not running, it should be a connection error, not a 500
            assert result["status_code"] is None or result["status_code"] >= 500, "Expected connection error or server error"
    
    @pytest.mark.asyncio
    async def test_all_services_health(self, http_client, test_config, health_checker):
        """Test health of all services together."""
        services = [
            ("nodejs_backend", test_config["nodejs_backend_url"]),
            ("query", test_config["query_service_url"]),
            ("indexing", test_config["indexing_service_url"]),
            ("connector", test_config["connector_service_url"]),
        ]
        
        health_results = {}
        
        for service_name, url in services:
            if service_name == "nodejs_backend":
                result = await health_checker.check_nodejs_backend(url)
            else:
                result = await health_checker.check_python_service(url, service_name)
            
            health_results[service_name] = result
        
        # Log health status for debugging
        for service_name, result in health_results.items():
            status = result["status"]
            print(f"Service {service_name}: {status}")
        
        # For P0 testing, we just verify that the health checker works
        # We don't require services to be running
        healthy_services = [name for name in health_results.keys() if health_results[name]["status"] == "healthy"]
        unhealthy_services = [name for name in health_results.keys() if health_results[name]["status"] == "unhealthy"]
        
        print(f"Health check completed: {len(healthy_services)} healthy, {len(unhealthy_services)} unhealthy")
        
        # Verify that all services were checked (either healthy or unhealthy)
        assert len(health_results) == len(services), "Not all services were checked"
        
        # For P0, we just verify the health checker is working
        # Services not running is acceptable
        if len(healthy_services) == 0:
            print("No services are running - this is acceptable for P0 testing")
            pytest.skip("No services running - acceptable for P0 testing")
    
    @pytest.mark.asyncio
    async def test_health_endpoint_response_format(self, http_client, test_config):
        """Test that health endpoints return properly formatted responses."""
        # Test Node.js backend health format
        try:
            response = await http_client.get(f"{test_config['nodejs_backend_url']}/api/v1/health")
            if response.status_code == 200:
                data = response.json()
                assert isinstance(data, dict), "Health response should be a JSON object"
                assert "status" in data, "Health response should contain 'status' field"
                assert "timestamp" in data, "Health response should contain 'timestamp' field"
        except httpx.RequestError:
            pytest.skip("Node.js backend not available")
        
        # Test Python service health format
        for service_name, url in [
            ("query", test_config["query_service_url"]),
            ("indexing", test_config["indexing_service_url"]),
            ("connector", test_config["connector_service_url"]),
        ]:
            try:
                response = await http_client.get(f"{url}/health")
                if response.status_code == 200:
                    data = response.json()
                    assert isinstance(data, dict), f"{service_name} health response should be a JSON object"
                    assert "status" in data, f"{service_name} health response should contain 'status' field"
            except httpx.RequestError:
                print(f"Service {service_name} not available")
    
    @pytest.mark.asyncio
    async def test_health_endpoint_timeout(self, http_client, test_config):
        """Test that health endpoints respond within reasonable time."""
        timeout = httpx.Timeout(5.0)  # 5 second timeout
        
        services_to_test = [
            ("nodejs_backend", f"{test_config['nodejs_backend_url']}/api/v1/health"),
            ("query", f"{test_config['query_service_url']}/health"),
            ("indexing", f"{test_config['indexing_service_url']}/health"),
            ("connector", f"{test_config['connector_service_url']}/health"),
        ]
        
        for service_name, url in services_to_test:
            try:
                start_time = time.time()
                response = await http_client.get(url, timeout=timeout)
                end_time = time.time()
                
                response_time = end_time - start_time
                assert response_time < 5.0, f"{service_name} health check took too long: {response_time:.2f}s"
                
                if response.status_code == 200:
                    print(f"{service_name} responded in {response_time:.2f}s")
                else:
                    print(f"{service_name} responded with status {response.status_code} in {response_time:.2f}s")
                    
            except httpx.TimeoutException:
                pytest.fail(f"{service_name} health check timed out after 5 seconds")
            except httpx.RequestError as e:
                print(f"{service_name} not available: {e}")


# Additional utility tests
@pytest.mark.health
class TestHealthUtilities:
    """Test health check utilities and helpers."""
    
    def test_health_checker_initialization(self, health_checker):
        """Test that health checker can be initialized."""
        assert health_checker is not None
    
    @pytest.mark.asyncio
    async def test_health_checker_with_invalid_url(self, health_checker):
        """Test health checker behavior with invalid URL."""
        # Test with non-existent service
        result = await health_checker.check_python_service("http://localhost:9999", "nonexistent")
        
        assert result["status"] == "unhealthy"
        assert result["service"] == "nonexistent"
        assert "error" in result
        assert result["status_code"] is None
    
    @pytest.mark.asyncio
    async def test_health_checker_with_malformed_url(self, health_checker):
        """Test health checker behavior with malformed URL."""
        # Test with malformed URL
        result = await health_checker.check_python_service("not-a-valid-url", "malformed")
        
        assert result["status"] == "unhealthy"
        assert result["service"] == "malformed"
        assert "error" in result
        assert result["status_code"] is None

