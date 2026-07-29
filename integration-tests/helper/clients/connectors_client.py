"""Connector API client for integration tests."""

from typing import Any
from urllib.parse import quote

import requests

from helper.http.api_client import APIClient


class ConnectorsClient(APIClient):
    """Client for /api/v1/connectors endpoints."""

    BASE = "/api/v1/connectors"

    def get_record_content(self, record_id: str, **options: Any) -> requests.Response:
        """Get a record's full parsed content and metadata.

        GET /record/{recordId}/content
        """
        return self.get(f"/record/{quote(record_id, safe='')}/content", **options)
