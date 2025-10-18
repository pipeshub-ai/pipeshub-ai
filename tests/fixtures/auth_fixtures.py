"""
Authentication-related fixtures for integration tests.

Provides fixtures for authentication tokens, user credentials, and auth helpers.
"""

from typing import Dict, Optional

import pytest  # type: ignore
from httpx import Response  # type: ignore

from tests.config.settings import get_settings
from tests.utils.assertions import HTTP_OK
from tests.utils.http_client import HTTPClient


@pytest.fixture(scope="session")
def test_user_credentials() -> Dict[str, str]:
    """
    Provide test user credentials.
    
    Returns:
        Dictionary with username and password
        
    Example:
        def test_login(http_client, test_user_credentials):
            response = http_client.post("/auth/login", json=test_user_credentials)
            assert response.status_code == 200
    """
    settings = get_settings()
    return {
        "username": settings.auth.test_username,
        "password": settings.auth.test_password,
    }


@pytest.fixture(scope="session")
def admin_user_credentials() -> Dict[str, str]:
    """
    Provide admin user credentials.
    
    Returns:
        Dictionary with admin username and password
        
    Example:
        def test_admin_login(http_client, admin_user_credentials):
            response = http_client.post("/auth/login", json=admin_user_credentials)
            assert response.status_code == 200
    """
    settings = get_settings()
    return {
        "username": settings.auth.admin_username,
        "password": settings.auth.admin_password,
    }


@pytest.fixture(scope="function")
def auth_token(http_client: HTTPClient, test_user_credentials: Dict[str, str]) -> Optional[str]:
    """
    Obtain authentication token for test user.
    
    Args:
        http_client: HTTP client fixture
        test_user_credentials: Test user credentials fixture
        
    Returns:
        Authentication token or None if authentication fails
        
    Example:
        def test_with_token(http_client, auth_token):
            http_client.set_auth_token(auth_token)
            response = http_client.get("/api/protected")
            assert response.status_code == 200
    """
    response: Response = http_client.post("/auth/login", json=test_user_credentials)
    
    if response.status_code == HTTP_OK:
        data: Dict = response.json()
        return data.get("access_token") or data.get("token")
    
    return None


@pytest.fixture(scope="function")
def admin_auth_token(http_client: HTTPClient, admin_user_credentials: Dict[str, str]) -> Optional[str]:
    """
    Obtain authentication token for admin user.
    
    Args:
        http_client: HTTP client fixture
        admin_user_credentials: Admin credentials fixture
        
    Returns:
        Admin authentication token or None if authentication fails
        
    Example:
        def test_with_admin_token(http_client, admin_auth_token):
            http_client.set_auth_token(admin_auth_token)
            response = http_client.get("/api/admin/users")
            assert response.status_code == 200
    """
    response: Response = http_client.post("/auth/login", json=admin_user_credentials)
    
    if response.status_code == HTTP_OK:
        data: Dict = response.json()
        return data.get("access_token") or data.get("token")
    
    return None


@pytest.fixture(scope="function")
def auth_headers(auth_token: Optional[str]) -> Dict[str, str]:
    """
    Provide authentication headers.
    
    Args:
        auth_token: Authentication token fixture
        
    Returns:
        Dictionary with Authorization header
        
    Example:
        def test_with_auth_headers(http_client, auth_headers):
            response = http_client.get("/api/protected", headers=auth_headers)
            assert response.status_code == 200
    """
    if auth_token:
        return {"Authorization": f"Bearer {auth_token}"}
    return {}


@pytest.fixture(scope="function")
def admin_auth_headers(admin_auth_token: Optional[str]) -> Dict[str, str]:
    """
    Provide admin authentication headers.
    
    Args:
        admin_auth_token: Admin authentication token fixture
        
    Returns:
        Dictionary with Authorization header
        
    Example:
        def test_with_admin_headers(http_client, admin_auth_headers):
            response = http_client.get("/api/admin/users", headers=admin_auth_headers)
            assert response.status_code == 200
    """
    if admin_auth_token:
        return {"Authorization": f"Bearer {admin_auth_token}"}
    return {}


class AuthHelper:
    """Helper class for authentication operations in tests."""
    
    def __init__(self, http_client: HTTPClient) -> None:
        """
        Initialize auth helper.
        
        Args:
            http_client: HTTP client for making requests
        """
        self.client = http_client
        self.settings = get_settings()
    
    def login(self, username: str, password: str) -> Optional[str]:
        """
        Perform login and return token.
        
        Args:
            username: User username
            password: User password
            
        Returns:
            Authentication token or None
        """
        response: Response = self.client.post(
            "/auth/login",
            json={"username": username, "password": password}
        )
        
        if response.status_code == HTTP_OK:
            data: Dict = response.json()
            return data.get("access_token") or data.get("token")
        
        return None
    
    def logout(self, token: str) -> bool:
        """
        Perform logout.
        
        Args:
            token: Authentication token
            
        Returns:
            True if logout successful
        """
        self.client.set_auth_token(token)
        response: Response = self.client.post("/auth/logout")
        self.client.clear_auth_token()
        
        return response.status_code in [200, 204]
    
    def refresh_token(self, refresh_token: str) -> Optional[str]:
        """
        Refresh authentication token.
        
        Args:
            refresh_token: Refresh token
            
        Returns:
            New access token or None
        """
        response: Response = self.client.post(
            "/auth/refresh",
            json={"refresh_token": refresh_token}
        )
        
        if response.status_code == HTTP_OK:
            data: Dict = response.json()
            return data.get("access_token") or data.get("token")
        
        return None
    
    def register_user(self, username: str, password: str, email: str) -> bool:
        """
        Register a new user.
        
        Args:
            username: Username
            password: Password
            email: Email address
            
        Returns:
            True if registration successful
        """
        response: Response = self.client.post(
            "/auth/register",
            json={
                "username": username,
                "password": password,
                "email": email,
            }
        )
        
        return response.status_code in [200, 201]


@pytest.fixture(scope="function")
def auth_helper(http_client: HTTPClient) -> AuthHelper:
    """
    Provide authentication helper.
    
    Args:
        http_client: HTTP client fixture
        
    Returns:
        AuthHelper instance
        
    Example:
        def test_user_flow(auth_helper):
            token = auth_helper.login("user", "pass")
            assert token is not None
            assert auth_helper.logout(token)
    """
    return AuthHelper(http_client)

