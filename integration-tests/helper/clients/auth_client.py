"""Auth Config API client for integration tests."""

from typing import Any

import requests

from helper.http.api_client import APIClient


class AuthClient(APIClient):
    """Client for /api/v1/orgAuthConfig endpoints.

    Provides both low-level HTTP methods and higher-level domain methods
    for organization auth configuration operations.
    """

    BASE = "/api/v1/orgAuthConfig"

    def get_auth_methods(self, **kwargs: Any) -> requests.Response:
        """Get available authentication methods for the organization."""
        return self.get("/authMethods", **kwargs)

    def setup_auth_config(self, **kwargs: Any) -> requests.Response:
        """Set up authentication configuration for the organization.

        Args:
            **kwargs: Auth config fields, or request kwargs (auth=, headers=, json=)
        """
        return self.post("/", **kwargs)

    def update_auth_method(self, **kwargs: Any) -> requests.Response:
        """Update an authentication method.

        Args:
            **kwargs: Auth method update fields, or request kwargs (auth=, headers=, json=)
        """
        return self.post("/updateAuthMethod", **kwargs)


class UserAccountClient(APIClient):
    """Client for /api/v1/userAccount endpoints.

    Provides methods for user account management operations.
    """

    BASE = "/api/v1/userAccount"

    def init_auth(self, email: str) -> requests.Response:
        """POST /initAuth — start session-based login (no OAuth)."""
        return self.post("/initAuth", json={"email": email}, auth=False)

    def authenticate(
        self,
        session_token: str,
        email: str,
        password: str,
        *,
        extra_json: dict[str, Any] | None = None,
    ) -> requests.Response:
        """POST /authenticate with x-session-token (no OAuth)."""
        body: dict[str, Any] = {
            "method": "password",
            "credentials": {"password": password},
            "email": email,
        }
        if extra_json:
            body.update(extra_json)
        return self.post(
            "/authenticate",
            headers={"x-session-token": session_token},
            json=body,
            auth=False,
        )

    def reset_password(
        self,
        access_token: str,
        current_password: str,
        new_password: str,
    ) -> requests.Response:
        """POST /password/reset with session Bearer token."""
        return self.post(
            "/password/reset",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={
                "currentPassword": current_password,
                "newPassword": new_password,
            },
            auth=False,
        )

    def logout_manual(self, access_token: str) -> requests.Response:
        """POST /logout/manual with session Bearer token."""
        return self.post(
            "/logout/manual",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            auth=False,
        )

    def refresh_token(self, refresh_token: str) -> requests.Response:
        """POST /refresh/token using refreshToken as Bearer."""
        return self.post(
            "/refresh/token",
            headers={
                "Authorization": f"Bearer {refresh_token}",
                "Content-Type": "application/json",
            },
            auth=False,
        )

    def get_account(self) -> requests.Response:
        """Get the authenticated user's account."""
        return self.get("/")

    def update_account(self, **kwargs: Any) -> requests.Response:
        """Update the authenticated user's account.

        Args:
            **kwargs: Account update fields
        """
        return self.put("/", json=kwargs)

    def change_password(
        self,
        current_password: str,
        new_password: str,
    ) -> requests.Response:
        """Change the authenticated user's password.

        Args:
            current_password: Current password
            new_password: New password
        """
        return self.post(
            "/change-password",
            json={
                "currentPassword": current_password,
                "newPassword": new_password,
            },
        )
