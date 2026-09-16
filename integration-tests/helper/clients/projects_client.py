"""Projects API client for integration tests."""

from typing import Any

import requests

from helper.http.api_client import APIClient


class ProjectsClient(APIClient):
    """Client for /api/v1/projects endpoints.

    Provides both low-level HTTP methods (inherited get/post/put/patch/delete)
    and higher-level domain methods mirroring the Node `project.routes.ts`
    surface (see `backend/nodejs/apps/src/modules/projects/`).
    """

    BASE = "/api/v1/projects"

    def create_project(self, name: str, **kwargs: Any) -> requests.Response:
        """Create a project (POST /).

        Args:
            name: Project name.
            **kwargs: Additional fields (description, icon, color,
                      instructions, knowledgeScope, appliedFilters).
        """
        return self.post("/", json={"name": name, **kwargs})

    def list_projects(self, **params: Any) -> requests.Response:
        """List projects (GET /).

        Args:
            **params: Query params (page, limit, search, scope, includeArchived).
        """
        return self.get("/", params=params)

    def get_project(self, project_id: str) -> requests.Response:
        """Get project by id (GET /{projectId})."""
        return self.get(f"/{project_id}")

    def update_project(self, project_id: str, **kwargs: Any) -> requests.Response:
        """Update project metadata (PATCH /{projectId}).

        Args:
            project_id: Project id.
            **kwargs: Update fields (name, description, icon, color,
                      instructions, knowledgeScope, appliedFilters,
                      visibility, chatSharing — the last two are owner-only).
        """
        return self.patch(f"/{project_id}", json=kwargs)

    def delete_project(self, project_id: str) -> requests.Response:
        """Soft-delete a project (DELETE /{projectId})."""
        return self.delete(f"/{project_id}")

    def archive_project(self, project_id: str) -> requests.Response:
        """Archive a project (POST /{projectId}/archive)."""
        return self.post(f"/{project_id}/archive")

    def unarchive_project(self, project_id: str) -> requests.Response:
        """Unarchive a project (POST /{projectId}/unarchive)."""
        return self.post(f"/{project_id}/unarchive")

    def pin_project(self, project_id: str) -> requests.Response:
        """Pin a project (POST /{projectId}/pin)."""
        return self.post(f"/{project_id}/pin")

    def unpin_project(self, project_id: str) -> requests.Response:
        """Unpin a project (POST /{projectId}/unpin)."""
        return self.post(f"/{project_id}/unpin")

    def list_project_conversations(
        self, project_id: str, **params: Any
    ) -> requests.Response:
        """List a project's conversations (GET /{projectId}/conversations)."""
        return self.get(f"/{project_id}/conversations", params=params)

    def upload_files(
        self, project_id: str, *, files: Any, **kwargs: Any
    ) -> requests.Response:
        """Upload files to a project (multipart, POST /{projectId}/files)."""
        return self.post(f"/{project_id}/files", files=files, **kwargs)

    def delete_file(self, project_id: str, record_id: str) -> requests.Response:
        """Remove a file from a project (DELETE /{projectId}/files/{recordId})."""
        return self.delete(f"/{project_id}/files/{record_id}")

    def list_members(self, project_id: str) -> requests.Response:
        """List project members (GET /{projectId}/members)."""
        return self.get(f"/{project_id}/members")

    def upsert_members(
        self, project_id: str, members: list[dict[str, Any]]
    ) -> requests.Response:
        """Add/update project members (PUT /{projectId}/members).

        Args:
            project_id: Project id.
            members: List of {"principalId": str, "role": "viewer"|"editor"}.
        """
        return self.put(f"/{project_id}/members", json={"members": members})

    def remove_member(self, project_id: str, member_user_id: str) -> requests.Response:
        """Remove a project member (DELETE /{projectId}/members/{memberUserId})."""
        return self.delete(f"/{project_id}/members/{member_user_id}")
