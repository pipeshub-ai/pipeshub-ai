"""Teams API client for integration tests."""

from typing import Any

import requests

from helper.http.api_client import APIClient


class TeamsClient(APIClient):
    """Client for /api/v1/teams endpoints.

    Provides both low-level HTTP methods (get, post, put, delete) and
    higher-level domain methods (create_team, get_user_teams, etc.).
    """

    BASE = "/api/v1/teams"

    def create_team(self, name: str, **kwargs: Any) -> requests.Response:
        """Create a new team.

        Args:
            name: Team name
            **kwargs: Additional fields (description, userRoles, etc.)
        """
        return self.post("/", json={"name": name, **kwargs})

    def get_user_teams(self, **params: Any) -> requests.Response:
        """List teams for the authenticated user.

        Args:
            **params: Query params (page, limit, search, created_by, etc.)
        """
        return self.get("/user/teams", params=params)

    def get_team(self, team_id: str) -> requests.Response:
        """Get team by ID."""
        return self.get(f"/{team_id}")

    def update_team(self, team_id: str, **kwargs: Any) -> requests.Response:
        """Update team metadata and/or members.

        Args:
            team_id: Team UUID
            **kwargs: Update fields (name, description, addUserRoles,
                      updateUserRoles, removeUserIds)
        """
        return self.put(f"/{team_id}", json=kwargs)

    def delete_team(self, team_id: str) -> requests.Response:
        """Delete a team."""
        return self.delete(f"/{team_id}")

    def get_team_users(self, team_id: str, **params: Any) -> requests.Response:
        """List users in a team.

        Args:
            team_id: Team UUID
            **params: Query params (page, limit, search)
        """
        return self.get(f"/{team_id}/users", params=params)
