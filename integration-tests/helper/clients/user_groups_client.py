"""User Groups API client for integration tests."""

from typing import Any

import requests

from helper.http.api_client import APIClient


class UserGroupsClient(APIClient):
    """Client for /api/v1/userGroups endpoints.

    Provides both low-level HTTP methods (get, post, put, delete) and
    higher-level domain methods for user group management.
    """

    BASE = "/api/v1/userGroups"

    def get_all_groups(self, **params: Any) -> requests.Response:
        """List all user groups.

        Args:
            **params: Query params (page, limit, search, type, etc.)
        """
        return self.get("/", params=params)

    def get_group(self, group_id: str) -> requests.Response:
        """Get group by ID."""
        return self.get(f"/{group_id}")

    def create_group(
        self,
        name: str,
        group_type: str = "custom",
        **kwargs: Any,
    ) -> requests.Response:
        """Create a new user group.

        Args:
            name: Group name
            group_type: Group type (custom, department, etc.)
            **kwargs: Additional fields (description, etc.)
        """
        return self.post("/", json={"name": name, "type": group_type, **kwargs})

    def update_group(self, group_id: str, **kwargs: Any) -> requests.Response:
        """Update group by ID.

        Args:
            group_id: Group's MongoDB ID
            **kwargs: Fields to update (name, description, etc.)
        """
        return self.put(f"/{group_id}", json=kwargs)

    def delete_group(self, group_id: str) -> requests.Response:
        """Delete a user group."""
        return self.delete(f"/{group_id}")

    def add_users(
        self,
        user_ids: list[str],
        group_ids: list[str],
    ) -> requests.Response:
        """Add users to one or more groups."""
        return self.post(
            "/add-users",
            json={"userIds": user_ids, "groupIds": group_ids},
        )

    def remove_users(
        self,
        user_ids: list[str],
        group_ids: list[str],
    ) -> requests.Response:
        """Remove users from one or more groups."""
        return self.post(
            "/remove-users",
            json={"userIds": user_ids, "groupIds": group_ids},
        )

    def get_users_in_group(
        self,
        group_id: str,
        **params: Any,
    ) -> requests.Response:
        """Get users in a specific group.

        Args:
            group_id: Group's MongoDB ID
            **params: Query params (page, limit, etc.)
        """
        return self.get(f"/{group_id}/users", params=params)

    def get_groups_for_user(self, user_id: str) -> requests.Response:
        """Get groups that a user belongs to."""
        return self.get(f"/users/{user_id}")

    def get_group_statistics(self) -> requests.Response:
        """Get statistics for all groups."""
        return self.get("/stats/list")

    def health(self) -> requests.Response:
        """Check user groups service health (no auth required)."""
        return self.get("/health", auth=False)
