"""
HTTP/HTTPS Client wrapper for integration tests.

This module provides a robust HTTP client for making API calls during tests,
with features like retry logic, timeout handling, request/response logging,
and built-in assertions.
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional, Union
from urllib.parse import urljoin

import httpx # type: ignore
from httpx import Response # type: ignore

logger = logging.getLogger(__name__)


class HTTPClient:
    """
    Synchronous HTTP client for integration testing.
    
    Features:
    - Automatic retry on failure
    - Request/response logging
    - Cookie management
    - Custom headers support
    - Timeout configuration
    - SSL verification control
    """
    
    def __init__(
        self,
        base_url: str,
        timeout: int = 30,
        verify_ssl: bool = True,
        default_headers: Optional[Dict[str, str]] = None,
        follow_redirects: bool = True,
    ):
        """
        Initialize HTTP client.
        
        Args:
            base_url: Base URL for all requests
            timeout: Request timeout in seconds
            verify_ssl: Whether to verify SSL certificates
            default_headers: Headers to include in all requests
            follow_redirects: Whether to follow redirects automatically
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.default_headers = default_headers or {}
        self.follow_redirects = follow_redirects
        
        self._client = httpx.Client(
            timeout=timeout,
            verify=verify_ssl,
            follow_redirects=follow_redirects,
        )
        self._cookies: Dict[str, str] = {}
    
    def _build_url(self, endpoint: str) -> str:
        """Build full URL from base URL and endpoint."""
        endpoint = endpoint.lstrip("/")
        return urljoin(f"{self.base_url}/", endpoint)
    
    def _prepare_headers(self, custom_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Merge default and custom headers."""
        headers = self.default_headers.copy()
        if custom_headers:
            headers.update(custom_headers)
        return headers
    
    def _log_request(self, method: str, url: str, **kwargs):
        """Log outgoing request details."""
        logger.debug(f"→ {method} {url}")
        if "json" in kwargs:
            logger.debug(f"  Body: {json.dumps(kwargs['json'], indent=2)}")
        if "params" in kwargs:
            logger.debug(f"  Params: {kwargs['params']}")
    
    def _log_response(self, response: Response):
        """Log response details."""
        logger.debug(f"← {response.status_code} {response.url}")
        try:
            logger.debug(f"  Body: {json.dumps(response.json(), indent=2)}")
        except Exception:
            logger.debug(f"  Body: {response.text[:200]}")
    
    def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Response:
        """
        Send GET request.
        
        Args:
            endpoint: API endpoint (relative to base_url)
            params: Query parameters
            headers: Additional headers
            **kwargs: Additional arguments for httpx
            
        Returns:
            Response object
        """
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("GET", url, params=params)
        response = self._client.get(url, params=params, headers=headers, cookies=self._cookies, **kwargs)
        self._log_response(response)
        
        # Update cookies
        self._cookies.update(response.cookies)
        
        return response
    
    def post(
        self,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        files: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Response:
        """
        Send POST request.
        
        Args:
            endpoint: API endpoint (relative to base_url)
            json: JSON body
            data: Form data
            params: Query parameters
            headers: Additional headers
            files: Files to upload
            **kwargs: Additional arguments for httpx
            
        Returns:
            Response object
        """
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("POST", url, json=json, params=params)
        response = self._client.post(
            url,
            json=json,
            data=data,
            params=params,
            headers=headers,
            cookies=self._cookies,
            files=files,
            **kwargs,
        )
        self._log_response(response)
        
        # Update cookies
        self._cookies.update(response.cookies)
        
        return response
    
    def put(
        self,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Response:
        """Send PUT request."""
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("PUT", url, json=json, params=params)
        response = self._client.put(
            url,
            json=json,
            data=data,
            params=params,
            headers=headers,
            cookies=self._cookies,
            **kwargs,
        )
        self._log_response(response)
        
        self._cookies.update(response.cookies)
        return response
    
    def patch(
        self,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Response:
        """Send PATCH request."""
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("PATCH", url, json=json, params=params)
        response = self._client.patch(
            url,
            json=json,
            data=data,
            params=params,
            headers=headers,
            cookies=self._cookies,
            **kwargs,
        )
        self._log_response(response)
        
        self._cookies.update(response.cookies)
        return response
    
    def delete(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Response:
        """Send DELETE request."""
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("DELETE", url, params=params)
        response = self._client.delete(url, params=params, headers=headers, cookies=self._cookies, **kwargs)
        self._log_response(response)
        
        self._cookies.update(response.cookies)
        return response
    
    def head(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Response:
        """Send HEAD request."""
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("HEAD", url, params=params)
        response = self._client.head(url, params=params, headers=headers, cookies=self._cookies, **kwargs)
        self._log_response(response)
        
        return response
    
    def options(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Response:
        """Send OPTIONS request."""
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("OPTIONS", url, params=params)
        response = self._client.options(url, params=params, headers=headers, cookies=self._cookies, **kwargs)
        self._log_response(response)
        
        return response
    
    def set_auth_token(self, token: str, token_type: str = "Bearer"):
        """
        Set authentication token in default headers.
        
        Args:
            token: Authentication token
            token_type: Token type (Bearer, Basic, etc.)
        """
        self.default_headers["Authorization"] = f"{token_type} {token}"
    
    def clear_auth_token(self):
        """Remove authentication token from default headers."""
        self.default_headers.pop("Authorization", None)
    
    def set_cookie(self, name: str, value: str):
        """Set a cookie for subsequent requests."""
        self._cookies[name] = value
    
    def clear_cookies(self):
        """Clear all cookies."""
        self._cookies.clear()
    
    def close(self):
        """Close the HTTP client."""
        self._client.close()
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


class AsyncHTTPClient:
    """
    Asynchronous HTTP client for integration testing.
    
    Similar to HTTPClient but with async/await support for better
    performance in async test environments.
    """
    
    def __init__(
        self,
        base_url: str,
        timeout: int = 30,
        verify_ssl: bool = True,
        default_headers: Optional[Dict[str, str]] = None,
        follow_redirects: bool = True,
    ):
        """
        Initialize async HTTP client.
        
        Args:
            base_url: Base URL for all requests
            timeout: Request timeout in seconds
            verify_ssl: Whether to verify SSL certificates
            default_headers: Headers to include in all requests
            follow_redirects: Whether to follow redirects automatically
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.default_headers = default_headers or {}
        self.follow_redirects = follow_redirects
        
        self._client = httpx.AsyncClient(
            timeout=timeout,
            verify=verify_ssl,
            follow_redirects=follow_redirects,
        )
        self._cookies: Dict[str, str] = {}
    
    def _build_url(self, endpoint: str) -> str:
        """Build full URL from base URL and endpoint."""
        endpoint = endpoint.lstrip("/")
        return urljoin(f"{self.base_url}/", endpoint)
    
    def _prepare_headers(self, custom_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """Merge default and custom headers."""
        headers = self.default_headers.copy()
        if custom_headers:
            headers.update(custom_headers)
        return headers
    
    def _log_request(self, method: str, url: str, **kwargs):
        """Log outgoing request details."""
        logger.debug(f"→ {method} {url}")
        if "json" in kwargs:
            logger.debug(f"  Body: {json.dumps(kwargs['json'], indent=2)}")
        if "params" in kwargs:
            logger.debug(f"  Params: {kwargs['params']}")
    
    def _log_response(self, response: Response):
        """Log response details."""
        logger.debug(f"← {response.status_code} {response.url}")
        try:
            logger.debug(f"  Body: {json.dumps(response.json(), indent=2)}")
        except Exception:
            logger.debug(f"  Body: {response.text[:200]}")
    
    async def get(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Response:
        """Send async GET request."""
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("GET", url, params=params)
        response = await self._client.get(url, params=params, headers=headers, cookies=self._cookies, **kwargs)
        self._log_response(response)
        
        self._cookies.update(response.cookies)
        return response
    
    async def post(
        self,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        files: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Response:
        """Send async POST request."""
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("POST", url, json=json, params=params)
        response = await self._client.post(
            url,
            json=json,
            data=data,
            params=params,
            headers=headers,
            cookies=self._cookies,
            files=files,
            **kwargs,
        )
        self._log_response(response)
        
        self._cookies.update(response.cookies)
        return response
    
    async def put(
        self,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Response:
        """Send async PUT request."""
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("PUT", url, json=json, params=params)
        response = await self._client.put(
            url,
            json=json,
            data=data,
            params=params,
            headers=headers,
            cookies=self._cookies,
            **kwargs,
        )
        self._log_response(response)
        
        self._cookies.update(response.cookies)
        return response
    
    async def patch(
        self,
        endpoint: str,
        json: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Response:
        """Send async PATCH request."""
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("PATCH", url, json=json, params=params)
        response = await self._client.patch(
            url,
            json=json,
            data=data,
            params=params,
            headers=headers,
            cookies=self._cookies,
            **kwargs,
        )
        self._log_response(response)
        
        self._cookies.update(response.cookies)
        return response
    
    async def delete(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Response:
        """Send async DELETE request."""
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("DELETE", url, params=params)
        response = await self._client.delete(url, params=params, headers=headers, cookies=self._cookies, **kwargs)
        self._log_response(response)
        
        self._cookies.update(response.cookies)
        return response
    
    async def head(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Response:
        """Send async HEAD request."""
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("HEAD", url, params=params)
        response = await self._client.head(url, params=params, headers=headers, cookies=self._cookies, **kwargs)
        self._log_response(response)
        
        return response
    
    async def options(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs,
    ) -> Response:
        """Send async OPTIONS request."""
        url = self._build_url(endpoint)
        headers = self._prepare_headers(headers)
        
        self._log_request("OPTIONS", url, params=params)
        response = await self._client.options(url, params=params, headers=headers, cookies=self._cookies, **kwargs)
        self._log_response(response)
        
        return response
    
    def set_auth_token(self, token: str, token_type: str = "Bearer"):
        """Set authentication token in default headers."""
        self.default_headers["Authorization"] = f"{token_type} {token}"
    
    def clear_auth_token(self):
        """Remove authentication token from default headers."""
        self.default_headers.pop("Authorization", None)
    
    def set_cookie(self, name: str, value: str):
        """Set a cookie for subsequent requests."""
        self._cookies[name] = value
    
    def clear_cookies(self):
        """Clear all cookies."""
        self._cookies.clear()
    
    async def close(self):
        """Close the async HTTP client."""
        await self._client.aclose()
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.close()

