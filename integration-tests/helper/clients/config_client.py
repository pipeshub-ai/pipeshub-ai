"""Configuration Manager API client for integration tests."""

from typing import Any

import requests

from helper.http.api_client import APIClient


class ConfigClient(APIClient):
    """Client for /api/v1/configurationManager endpoints.

    Provides methods for managing various configuration settings.
    """

    BASE = "/api/v1/configurationManager"

    def get_web_search_providers(self) -> requests.Response:
        """Get web search providers and settings."""
        return self.get("/web-search")

    def update_web_search_settings(self, **kwargs: Any) -> requests.Response:
        """Update web search settings.

        Args:
            **kwargs: Settings fields (includeImages, maxImages, etc.)
        """
        return self.put("/web-search/settings", json=kwargs)

    def create_smtp_config(self, **kwargs: Any) -> requests.Response:
        """Create/update SMTP config (POST /smtpConfig).

        Body fields: host, port, username, password, fromEmail. This is the
        store the bulk-invite smtpConfigCheck middleware reads, so it must be
        set before the /users/bulk/invite* routes will accept a request.
        """
        return self.post("/smtpConfig", json=kwargs)

    def get_smtp_config(self) -> requests.Response:
        """Get SMTP config (GET /smtpConfig)."""
        return self.get("/smtpConfig")

    def get_email_settings(self) -> requests.Response:
        """Get email/SMTP settings."""
        return self.get("/email")

    def update_email_settings(self, **kwargs: Any) -> requests.Response:
        """Update email/SMTP settings.

        Args:
            **kwargs: Email settings fields
        """
        return self.put("/email", json=kwargs)

    def get_storage_settings(self) -> requests.Response:
        """Get storage settings."""
        return self.get("/storage")

    def update_storage_settings(self, **kwargs: Any) -> requests.Response:
        """Update storage settings.

        Args:
            **kwargs: Storage settings fields
        """
        return self.put("/storage", json=kwargs)
