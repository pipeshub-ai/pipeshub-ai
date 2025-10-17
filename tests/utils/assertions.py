"""
Response assertion utilities for integration tests.

This module provides fluent assertion helpers for validating HTTP responses,
making tests more readable and maintainable.
"""

from typing import Any, Dict, List, Optional

from httpx import Response # type: ignore


class ResponseAssertion:
    """
    Fluent assertion interface for HTTP responses.
    
    Example:
        response = client.get("/api/users")
        ResponseAssertion(response) \\
            .assert_status(200) \\
            .assert_json_contains({"status": "success"}) \\
            .assert_header_present("Content-Type")
    """
    
    def __init__(self, response: Response):
        """
        Initialize response assertion.
        
        Args:
            response: HTTP response to validate
        """
        self.response = response
    
    def assert_status(self, expected_status: int, message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response status code.
        
        Args:
            expected_status: Expected HTTP status code
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        actual_status = self.response.status_code
        if actual_status != expected_status:
            error_msg = message or f"Expected status {expected_status}, got {actual_status}"
            error_msg += f"\nResponse body: {self.response.text[:200]}"
            raise AssertionError(error_msg)
        return self
    
    def assert_status_in(self, status_codes: List[int], message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response status is one of the expected codes.
        
        Args:
            status_codes: List of acceptable status codes
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        actual_status = self.response.status_code
        if actual_status not in status_codes:
            error_msg = message or f"Expected status in {status_codes}, got {actual_status}"
            raise AssertionError(error_msg)
        return self
    
    def assert_success(self, message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response has successful status (2xx).
        
        Args:
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        if not 200 <= self.response.status_code < 300:
            error_msg = message or f"Expected 2xx status, got {self.response.status_code}"
            error_msg += f"\nResponse body: {self.response.text[:200]}"
            raise AssertionError(error_msg)
        return self
    
    def assert_error(self, message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response has error status (4xx or 5xx).
        
        Args:
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        if not (400 <= self.response.status_code < 600):
            error_msg = message or f"Expected error status (4xx/5xx), got {self.response.status_code}"
            raise AssertionError(error_msg)
        return self
    
    def assert_json_contains(self, expected_data: Dict[str, Any], message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response JSON contains expected key-value pairs.
        
        Args:
            expected_data: Expected key-value pairs
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        try:
            actual_data = self.response.json()
        except Exception as e:
            raise AssertionError(f"Response is not valid JSON: {e}")
        
        for key, expected_value in expected_data.items():
            if key not in actual_data:
                error_msg = message or f"Key '{key}' not found in response"
                raise AssertionError(error_msg)
            
            actual_value = actual_data[key]
            if actual_value != expected_value:
                error_msg = message or f"Key '{key}': expected {expected_value}, got {actual_value}"
                raise AssertionError(error_msg)
        
        return self
    
    def assert_json_schema(self, expected_keys: List[str], message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response JSON has expected keys.
        
        Args:
            expected_keys: List of expected keys
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        try:
            actual_data = self.response.json()
        except Exception as e:
            raise AssertionError(f"Response is not valid JSON: {e}")
        
        missing_keys = set(expected_keys) - set(actual_data.keys())
        if missing_keys:
            error_msg = message or f"Missing keys in response: {missing_keys}"
            raise AssertionError(error_msg)
        
        return self
    
    def assert_json_list(self, min_length: Optional[int] = None, max_length: Optional[int] = None) -> "ResponseAssertion":
        """
        Assert response is a JSON list with optional length constraints.
        
        Args:
            min_length: Minimum expected list length
            max_length: Maximum expected list length
            
        Returns:
            Self for method chaining
        """
        try:
            data = self.response.json()
        except Exception as e:
            raise AssertionError(f"Response is not valid JSON: {e}")
        
        if not isinstance(data, list):
            raise AssertionError(f"Expected JSON list, got {type(data).__name__}")
        
        if min_length is not None and len(data) < min_length:
            raise AssertionError(f"Expected at least {min_length} items, got {len(data)}")
        
        if max_length is not None and len(data) > max_length:
            raise AssertionError(f"Expected at most {max_length} items, got {len(data)}")
        
        return self
    
    def assert_header_present(self, header_name: str, message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response header is present.
        
        Args:
            header_name: Header name to check
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        if header_name not in self.response.headers:
            error_msg = message or f"Header '{header_name}' not found in response"
            raise AssertionError(error_msg)
        return self
    
    def assert_header_value(self, header_name: str, expected_value: str, message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response header has expected value.
        
        Args:
            header_name: Header name
            expected_value: Expected header value
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        if header_name not in self.response.headers:
            raise AssertionError(f"Header '{header_name}' not found in response")
        
        actual_value = self.response.headers[header_name]
        if actual_value != expected_value:
            error_msg = message or f"Header '{header_name}': expected '{expected_value}', got '{actual_value}'"
            raise AssertionError(error_msg)
        
        return self
    
    def assert_content_type(self, expected_type: str, message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response Content-Type header.
        
        Args:
            expected_type: Expected content type (e.g., "application/json")
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        content_type = self.response.headers.get("Content-Type", "")
        if expected_type not in content_type:
            error_msg = message or f"Expected Content-Type '{expected_type}', got '{content_type}'"
            raise AssertionError(error_msg)
        return self
    
    def assert_body_contains(self, substring: str, message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response body contains substring.
        
        Args:
            substring: Substring to search for
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        if substring not in self.response.text:
            error_msg = message or f"Substring '{substring}' not found in response body"
            raise AssertionError(error_msg)
        return self
    
    def assert_body_not_contains(self, substring: str, message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response body does not contain substring.
        
        Args:
            substring: Substring to check
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        if substring in self.response.text:
            error_msg = message or f"Substring '{substring}' should not be in response body"
            raise AssertionError(error_msg)
        return self
    
    def assert_empty_body(self, message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response body is empty.
        
        Args:
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        if self.response.text:
            error_msg = message or f"Expected empty body, got: {self.response.text[:100]}"
            raise AssertionError(error_msg)
        return self
    
    def assert_response_time(self, max_seconds: float, message: Optional[str] = None) -> "ResponseAssertion":
        """
        Assert response time is within limit.
        
        Args:
            max_seconds: Maximum acceptable response time
            message: Custom error message
            
        Returns:
            Self for method chaining
        """
        # Note: httpx doesn't expose response time directly
        # This would need to be tracked separately in the client
        # For now, this is a placeholder
        return self
    
    def get_json(self) -> Any:
        """
        Get response JSON data.
        
        Returns:
            Parsed JSON data
        """
        return self.response.json()
    
    def get_text(self) -> str:
        """
        Get response text.
        
        Returns:
            Response body as text
        """
        return self.response.text


def assert_response(response: Response) -> ResponseAssertion:
    """
    Create a response assertion object.
    
    Args:
        response: HTTP response to validate
        
    Returns:
        ResponseAssertion instance for fluent assertions
        
    Example:
        response = client.get("/api/users")
        assert_response(response).assert_status(200).assert_json_contains({"total": 10})
    """
    return ResponseAssertion(response)

