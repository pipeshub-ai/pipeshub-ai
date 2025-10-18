"""
HTTP client fixtures for integration tests.

Provides pre-configured HTTP clients for synchronous and asynchronous testing.
"""

from typing import (
    AsyncGenerator,
    Generator,
    Optional,  # type: ignore
)

import pytest  # type: ignore
from httpx import Response  # type: ignore

from tests.config.settings import get_settings
from tests.utils.assertions import HTTP_OK
from tests.utils.http_client import AsyncHTTPClient, HTTPClient


@pytest.fixture(scope="function")
def http_client() -> Generator[HTTPClient, None, None]:
    """
    Provide a synchronous HTTP client for testing.
    
    The client is configured with settings from the test configuration
    and automatically cleans up after the test.
    
    Yields:
        HTTPClient instance
        
    Example:
        def test_api_endpoint(http_client):
            response: Response = http_client.get("/api/users")
            assert response.status_code == 200
    """
    settings = get_settings()
    
    client: HTTPClient = HTTPClient(
        base_url=settings.api.base_url,
        timeout=settings.api.timeout,
        verify_ssl=settings.api.verify_ssl,
        default_headers={"Content-Type": "application/json"},
    )
    
    yield client
    
    client.close()


@pytest.fixture(scope="function")
async def async_http_client() -> AsyncGenerator[AsyncHTTPClient, None]:
    """
    Provide an asynchronous HTTP client for testing.
    
    The client is configured with settings from the test configuration
    and automatically cleans up after the test.
    
    Yields:
        AsyncHTTPClient instance
        
    Example:
        async def test_api_endpoint(async_http_client):
            response = await async_http_client.get("/api/users")
            assert response.status_code == 200
    """
    settings = get_settings()
    
    client: AsyncHTTPClient = AsyncHTTPClient(
        base_url=settings.api.base_url,
        timeout=settings.api.timeout,
        verify_ssl=settings.api.verify_ssl,
        default_headers={"Content-Type": "application/json"},
    )
    
    yield client
    
    await client.close()


@pytest.fixture(scope="session")
def http_client_session() -> Generator[HTTPClient, None, None]:
    """
    Provide a session-scoped HTTP client.
    
    This client is shared across all tests in a session and is useful
    for tests that need to maintain state (cookies, auth tokens) across
    multiple test functions.
    
    Yields:
        HTTPClient instance
        
    Example:
        def test_login(http_client_session):
            response = http_client_session.post("/auth/login", json={...})
            # Session maintains authentication for subsequent tests
    """
    settings = get_settings()
    
    client: HTTPClient = HTTPClient(
        base_url=settings.api.base_url,
        timeout=settings.api.timeout,
        verify_ssl=settings.api.verify_ssl,
        default_headers={"Content-Type": "application/json"},
    )
    
    yield client
    
    client.close()


@pytest.fixture(scope="function")
def authenticated_http_client(http_client: HTTPClient) -> Generator[HTTPClient, None, None]:
    """
    Provide an HTTP client with authentication token.
    
    This fixture automatically authenticates the client before the test
    and clears authentication afterwards.
    
    Args:
        http_client: Base HTTP client fixture
        
    Yields:
        Authenticated HTTPClient instance
        
    Example:
        def test_protected_endpoint(authenticated_http_client):
            response = authenticated_http_client.get("/api/protected")
            assert response.status_code == 200
    """
    settings = get_settings()

    login_response: Response = http_client.post(
        "/auth/login",
        json={
            "username": settings.auth.test_username,
            "password": settings.auth.test_password,
        }
    )
    
    if login_response.status_code == HTTP_OK:
        token: Optional[str] = login_response.json().get("access_token") or login_response.json().get("token")
        if token:
            http_client.set_auth_token(token)
    
    yield http_client
    
    # Clear authentication
    http_client.clear_auth_token()


@pytest.fixture(scope="function")
async def authenticated_async_http_client(async_http_client: AsyncHTTPClient) -> AsyncGenerator[AsyncHTTPClient, None]:
    """
    Provide an async HTTP client with authentication token.
    
    This fixture automatically authenticates the client before the test
    and clears authentication afterwards.
    
    Args:
        async_http_client: Base async HTTP client fixture
        
    Yields:
        Authenticated AsyncHTTPClient instance
        
    Example:
        async def test_protected_endpoint(authenticated_async_http_client):
            response = await authenticated_async_http_client.get("/api/protected")
            assert response.status_code == 200
    """
    settings = get_settings()
    
    # Perform authentication
    login_response: Response = await async_http_client.post(
        "/auth/login",
        json={
            "username": settings.auth.test_username,
            "password": settings.auth.test_password,
        }
    )
    
    if login_response.status_code == HTTP_OK:
        token: Optional[str] = login_response.json().get("access_token") or login_response.json().get("token")
        if token:
            async_http_client.set_auth_token(token)
    
    yield async_http_client
    
    # Clear authentication
    async_http_client.clear_auth_token()


@pytest.fixture(scope="function")
def admin_http_client(http_client: HTTPClient) -> Generator[HTTPClient, None, None]:
    """
    Provide an HTTP client authenticated as admin.
    
    Args:
        http_client: Base HTTP client fixture
        
    Yields:
        Admin-authenticated HTTPClient instance
        
    Example:
        def test_admin_endpoint(admin_http_client):
            response = admin_http_client.get("/api/admin/users")
            assert response.status_code == 200
    """
    settings = get_settings()
    
    # Perform admin authentication
    login_response: Response = http_client.post(
        "/auth/login",
        json={
            "username": settings.auth.admin_username,
            "password": settings.auth.admin_password,
        }
    )
    
    if login_response.status_code == HTTP_OK:
        token: Optional[str] = login_response.json().get("access_token") or login_response.json().get("token")
        if token:
            http_client.set_auth_token(token)
    
    yield http_client
    
    # Clear authentication
    http_client.clear_auth_token()

