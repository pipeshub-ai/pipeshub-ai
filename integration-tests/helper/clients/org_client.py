"""Organization API client for integration tests."""

from typing import Any

import requests

from helper.http.api_client import APIClient


class OrgClient(APIClient):
    """Client for /api/v1/org endpoints.

    Provides both low-level HTTP methods (get, post, put, delete) and
    higher-level domain methods for organization management.
    """

    BASE = "/api/v1/org"

    def check_exists(self) -> requests.Response:
        """Check if organization exists (no auth required)."""
        return self.get("/exists", auth=False)

    def health(self) -> requests.Response:
        """Check organization service health (no auth required)."""
        return self.get("/health", auth=False)

    def get_organization(self) -> requests.Response:
        """Get the authenticated user's organization."""
        return self.get("/")

    def create_organization(self, **kwargs: Any) -> requests.Response:
        """Create a new organization.

        Args:
            **kwargs: Organization fields (name, etc.)
        """
        return self.post("/", json=kwargs)

    def update_organization(self, **kwargs: Any) -> requests.Response:
        """Update organization details.

        Args:
            **kwargs: Fields to update (name, description, etc.)
        """
        return self.put("/", json=kwargs)

    def get_onboarding_status(self) -> requests.Response:
        """Get organization's onboarding status."""
        return self.get("/onboarding-status")

    def update_onboarding_status(self, **kwargs: Any) -> requests.Response:
        """Update organization's onboarding status.

        Args:
            **kwargs: Onboarding status fields
        """
        return self.put("/onboarding-status", json=kwargs)

    def upload_logo(
        self,
        image_data: bytes,
        filename: str = "logo.png",
        content_type: str = "image/png",
    ) -> requests.Response:
        """Upload organization logo via multipart form upload."""
        return self.put(
            "/logo",
            files={"file": (filename, image_data, content_type)},
        )

    def get_logo(self) -> requests.Response:
        """Get organization logo (returns binary data or 204 if none)."""
        return self.get("/logo")

    def remove_logo(self) -> requests.Response:
        """Remove organization logo."""
        return self.delete("/logo")
