"""
API helper utilities for integration tests.
"""
import asyncio
import time
from typing import Dict, Any, List, Optional, Tuple
import pytest
import httpx


class APIHelper:
    """Helper class for API testing utilities."""
    
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
    
    async def health_check(self, endpoint: str = "/health") -> Dict[str, Any]:
        """Perform a health check on the API."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(f"{self.base_url}{endpoint}")
                return {
                    "status": "healthy" if response.status_code == 200 else "unhealthy",
                    "status_code": response.status_code,
                    "response": response.json() if response.status_code == 200 else None,
                    "response_time": response.elapsed.total_seconds()
                }
            except Exception as e:
                return {
                    "status": "unhealthy",
                    "error": str(e),
                    "status_code": None,
                    "response_time": None
                }
    
    async def get(self, endpoint: str, params: Dict = None, headers: Dict = None) -> Dict[str, Any]:
        """Perform a GET request."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(
                    f"{self.base_url}{endpoint}",
                    params=params,
                    headers=headers
                )
                return {
                    "status_code": response.status_code,
                    "response": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
                    "headers": dict(response.headers),
                    "response_time": response.elapsed.total_seconds()
                }
            except Exception as e:
                return {
                    "error": str(e),
                    "status_code": None,
                    "response_time": None
                }
    
    async def post(self, endpoint: str, data: Dict = None, json: Dict = None, headers: Dict = None) -> Dict[str, Any]:
        """Perform a POST request."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}{endpoint}",
                    data=data,
                    json=json,
                    headers=headers
                )
                return {
                    "status_code": response.status_code,
                    "response": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
                    "headers": dict(response.headers),
                    "response_time": response.elapsed.total_seconds()
                }
            except Exception as e:
                return {
                    "error": str(e),
                    "status_code": None,
                    "response_time": None
                }
    
    async def put(self, endpoint: str, data: Dict = None, json: Dict = None, headers: Dict = None) -> Dict[str, Any]:
        """Perform a PUT request."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.put(
                    f"{self.base_url}{endpoint}",
                    data=data,
                    json=json,
                    headers=headers
                )
                return {
                    "status_code": response.status_code,
                    "response": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
                    "headers": dict(response.headers),
                    "response_time": response.elapsed.total_seconds()
                }
            except Exception as e:
                return {
                    "error": str(e),
                    "status_code": None,
                    "response_time": None
                }
    
    async def delete(self, endpoint: str, headers: Dict = None) -> Dict[str, Any]:
        """Perform a DELETE request."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.delete(
                    f"{self.base_url}{endpoint}",
                    headers=headers
                )
                return {
                    "status_code": response.status_code,
                    "response": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
                    "headers": dict(response.headers),
                    "response_time": response.elapsed.total_seconds()
                }
            except Exception as e:
                return {
                    "error": str(e),
                    "status_code": None,
                    "response_time": None
                }


class ServiceHealthChecker:
    """Enhanced service health checker with retry logic."""
    
    def __init__(self, timeout: float = 30.0, retry_attempts: int = 3, retry_delay: float = 2.0):
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
    
    async def check_service_health(self, base_url: str, endpoint: str = "/health") -> Dict[str, Any]:
        """Check service health with retry logic."""
        for attempt in range(self.retry_attempts):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.get(f"{base_url.rstrip('/')}{endpoint}")
                    
                    if response.status_code == 200:
                        return {
                            "status": "healthy",
                            "status_code": response.status_code,
                            "response": response.json(),
                            "attempt": attempt + 1,
                            "response_time": response.elapsed.total_seconds()
                        }
                    else:
                        if attempt == self.retry_attempts - 1:
                            return {
                                "status": "unhealthy",
                                "status_code": response.status_code,
                                "response": response.text,
                                "attempt": attempt + 1,
                                "response_time": response.elapsed.total_seconds()
                            }
                        else:
                            await asyncio.sleep(self.retry_delay)
                            
            except Exception as e:
                if attempt == self.retry_attempts - 1:
                    return {
                        "status": "unhealthy",
                        "error": str(e),
                        "attempt": attempt + 1,
                        "response_time": None
                    }
                else:
                    await asyncio.sleep(self.retry_delay)
        
        return {
            "status": "unhealthy",
            "error": "Max retry attempts exceeded",
            "attempt": self.retry_attempts,
            "response_time": None
        }
    
    async def check_multiple_services(self, services: List[Tuple[str, str]]) -> Dict[str, Dict[str, Any]]:
        """Check health of multiple services concurrently."""
        tasks = []
        for service_name, base_url in services:
            task = self.check_service_health(base_url)
            tasks.append((service_name, task))
        
        results = {}
        for service_name, task in tasks:
            try:
                result = await task
                results[service_name] = result
            except Exception as e:
                results[service_name] = {
                    "status": "unhealthy",
                    "error": str(e),
                    "attempt": 1,
                    "response_time": None
                }
        
        return results


class PerformanceTester:
    """Performance testing utilities for APIs."""
    
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout
    
    async def measure_response_time(self, base_url: str, endpoint: str, method: str = "GET", 
                                  data: Dict = None, json: Dict = None) -> Dict[str, Any]:
        """Measure response time for an API endpoint."""
        start_time = time.time()
        
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                if method.upper() == "GET":
                    response = await client.get(f"{base_url.rstrip('/')}{endpoint}")
                elif method.upper() == "POST":
                    response = await client.post(f"{base_url.rstrip('/')}{endpoint}", data=data, json=json)
                elif method.upper() == "PUT":
                    response = await client.put(f"{base_url.rstrip('/')}{endpoint}", data=data, json=json)
                elif method.upper() == "DELETE":
                    response = await client.delete(f"{base_url.rstrip('/')}{endpoint}")
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")
                
                end_time = time.time()
                response_time = end_time - start_time
                
                return {
                    "status_code": response.status_code,
                    "response_time": response_time,
                    "success": response.status_code < 400,
                    "response_size": len(response.content) if hasattr(response, 'content') else 0
                }
        except Exception as e:
            end_time = time.time()
            response_time = end_time - start_time
            
            return {
                "error": str(e),
                "response_time": response_time,
                "success": False,
                "response_size": 0
            }
    
    async def load_test(self, base_url: str, endpoint: str, concurrent_requests: int = 10,
                       method: str = "GET", data: Dict = None, json: Dict = None) -> Dict[str, Any]:
        """Perform a simple load test on an API endpoint."""
        tasks = []
        for _ in range(concurrent_requests):
            task = self.measure_response_time(base_url, endpoint, method, data, json)
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Analyze results
        successful_requests = [r for r in results if not isinstance(r, Exception) and r.get("success", False)]
        failed_requests = [r for r in results if isinstance(r, Exception) or not r.get("success", False)]
        
        if successful_requests:
            response_times = [r["response_time"] for r in successful_requests]
            avg_response_time = sum(response_times) / len(response_times)
            min_response_time = min(response_times)
            max_response_time = max(response_times)
        else:
            avg_response_time = 0
            min_response_time = 0
            max_response_time = 0
        
        return {
            "total_requests": concurrent_requests,
            "successful_requests": len(successful_requests),
            "failed_requests": len(failed_requests),
            "success_rate": len(successful_requests) / concurrent_requests * 100,
            "avg_response_time": avg_response_time,
            "min_response_time": min_response_time,
            "max_response_time": max_response_time,
            "results": results
        }


class APIAssertions:
    """Common API assertions for testing."""
    
    @staticmethod
    def assert_health_response(response: Dict[str, Any], expected_status: str = "healthy"):
        """Assert that a health check response is valid."""
        assert "status" in response, "Health response should contain 'status' field"
        assert response["status"] == expected_status, f"Expected status '{expected_status}', got '{response['status']}'"
        
        if "response" in response and response["response"]:
            assert isinstance(response["response"], dict), "Health response should be a dictionary"
    
    @staticmethod
    def assert_successful_response(response: Dict[str, Any], expected_status_code: int = 200):
        """Assert that an API response is successful."""
        assert "status_code" in response, "Response should contain 'status_code' field"
        assert response["status_code"] == expected_status_code, f"Expected status code {expected_status_code}, got {response['status_code']}"
        
        if "error" in response:
            pytest.fail(f"Response contains error: {response['error']}")
    
    @staticmethod
    def assert_error_response(response: Dict[str, Any], expected_status_code: int = 400):
        """Assert that an API response is an error."""
        assert "status_code" in response, "Response should contain 'status_code' field"
        assert response["status_code"] == expected_status_code, f"Expected error status code {expected_status_code}, got {response['status_code']}"
        
        if "response" in response and response["response"]:
            assert "error" in response["response"] or "detail" in response["response"], "Error response should contain error details"
    
    @staticmethod
    def assert_response_time(response: Dict[str, Any], max_time: float = 5.0):
        """Assert that response time is within acceptable limits."""
        if "response_time" in response and response["response_time"] is not None:
            assert response["response_time"] <= max_time, f"Response time {response['response_time']:.2f}s exceeds maximum {max_time}s"
    
    @staticmethod
    def assert_json_response(response: Dict[str, Any]):
        """Assert that response contains valid JSON."""
        if "response" in response and response["response"]:
            assert isinstance(response["response"], (dict, list)), "Response should be valid JSON (dict or list)"
    
    @staticmethod
    def assert_required_fields(response: Dict[str, Any], required_fields: List[str]):
        """Assert that response contains required fields."""
        if "response" in response and isinstance(response["response"], dict):
            for field in required_fields:
                assert field in response["response"], f"Response missing required field: {field}"

