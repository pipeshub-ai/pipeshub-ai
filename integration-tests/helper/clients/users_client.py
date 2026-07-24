"""Users API client for integration tests."""

import json
from typing import Any, Optional

import requests

from helper.http.api_client import APIClient


class UsersClient(APIClient):
    """Client for /api/v1/users endpoints.

    Provides both low-level HTTP methods (get, post, put, delete, patch) and
    higher-level domain methods for user management operations.
    """

    BASE = "/api/v1/users"

    def get_all_users(self, **params: Any) -> requests.Response:
        """List all users with optional filters.

        Args:
            **params: Query params (page, limit, isBlocked, search, etc.)
        """
        return self.get("/", params=params)

    def get_user(self, user_id: str) -> requests.Response:
        """Get user by ID."""
        return self.get(f"/{user_id}")

    def get_user_email(self, user_id: str) -> requests.Response:
        """Get user's email by user ID."""
        return self.get(f"/{user_id}/email")

    def admin_check(self, user_id: str) -> requests.Response:
        """Check if user is admin."""
        return self.get(f"/{user_id}/adminCheck")

    def get_users_with_groups(self, **params: Any) -> requests.Response:
        """Get all users with their group memberships."""
        return self.get("/fetch/with-groups", params=params)

    def graph_list(self, **params: Any) -> requests.Response:
        """List users from the organization graph.

        Args:
            **params: Query params (page, limit, search, isActive, etc.)
        """
        return self.get("/graph/list", params=params)

    def health(self) -> requests.Response:
        """Check users service health (no auth required)."""
        return self.get("/health", auth=False)

    def create_user(
        self,
        email: str,
        full_name: str,
        **kwargs: Any,
    ) -> requests.Response:
        """Create a new user.

        Args:
            email: User's email address
            full_name: User's full name (fullName in API)
            **kwargs: Additional fields (firstName, lastName, designation, etc.)
        """
        return self.post("/", json={"email": email, "fullName": full_name, **kwargs})

    def update_user(self, user_id: str, **kwargs: Any) -> requests.Response:
        """Update user by ID.

        Args:
            user_id: User's MongoDB ID
            **kwargs: Fields to update (fullName, firstName, lastName, etc.)
        """
        return self.put(f"/{user_id}", json=kwargs)

    def delete_user(self, user_id: str) -> requests.Response:
        """Soft-delete a user."""
        return self.delete(f"/{user_id}")

    def update_full_name(self, user_id: str, full_name: str) -> requests.Response:
        """Update user's full name."""
        return self.patch(f"/{user_id}/fullname", json={"fullName": full_name})

    def update_first_name(self, user_id: str, first_name: str) -> requests.Response:
        """Update user's first name."""
        return self.patch(f"/{user_id}/firstName", json={"firstName": first_name})

    def update_last_name(self, user_id: str, last_name: str) -> requests.Response:
        """Update user's last name."""
        return self.patch(f"/{user_id}/lastName", json={"lastName": last_name})

    def update_designation(self, user_id: str, designation: str) -> requests.Response:
        """Update user's designation."""
        return self.patch(f"/{user_id}/designation", json={"designation": designation})

    def update_email(self, user_id: str, email: str) -> requests.Response:
        """Update user's email."""
        return self.patch(f"/{user_id}/email", json={"email": email})

    def upload_display_picture(
        self,
        image_data: bytes,
        filename: str = "avatar.png",
        content_type: str = "image/png",
    ) -> requests.Response:
        """Upload user's display picture via multipart form upload."""
        return self.put(
            "/dp",
            files={"file": (filename, image_data, content_type)},
        )

    def get_display_picture(self) -> requests.Response:
        """Get user's display picture (returns binary data)."""
        return self.get("/dp")

    def remove_display_picture(self) -> requests.Response:
        """Remove user's display picture."""
        return self.delete("/dp")

    def get_users_by_ids(self, user_ids: list[str]) -> requests.Response:
        """Get multiple users by their MongoDB ObjectIds."""
        return self.post("/by-ids", json={"userIds": user_ids})

    def unblock_user(self, user_id: str) -> requests.Response:
        """Unblock a blocked user."""
        return self.put(f"/{user_id}/unblock")

    def invite_bulk(
        self,
        emails: list[str],
        group_ids: Optional[list[str]] = None,
    ) -> requests.Response:
        """Invite users from an email list (POST /bulk/invite)."""
        body: dict[str, Any] = {"emails": emails}
        if group_ids is not None:
            body["groupIds"] = group_ids
        return self.post("/bulk/invite", json=body)

    def invite_bulk_upload(
        self,
        file_bytes: bytes,
        filename: str = "invites.csv",
        content_type: str = "text/csv",
        group_ids: Optional[list[str]] = None,
    ) -> requests.Response:
        """Invite users from an uploaded CSV/Excel file (POST /bulk/invite/upload).

        groupIds travels as a multipart form field (JSON-encoded), matching how
        the controller parses req.body.groupIds.
        """
        files = {"file": (filename, file_bytes, content_type)}
        data: dict[str, str] = {}
        if group_ids is not None:
            data["groupIds"] = json.dumps(group_ids)
        return self.post("/bulk/invite/upload", files=files, data=data)
