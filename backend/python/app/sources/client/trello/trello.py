import os
from typing import Any

import httpx

TRELLO_BASE_URL = "https://api.trello.com/1"


class TrelloClient:
    """Simple REST client for Trello API using key + token authentication.
    """

    def __init__(self, api_key: str | None = None, token: str | None = None) -> None:
        self.api_key = api_key or os.getenv("TRELLO_API_KEY")
        self.token = token or os.getenv("TRELLO_TOKEN")

        if not self.api_key or not self.token:
            raise ValueError("TRELLO_API_KEY and TRELLO_TOKEN must be provided")

        self.client = httpx.Client(base_url=TRELLO_BASE_URL, timeout=30)

    def _auth(self) -> dict[str, Any]:
        return {"key": self.api_key, "token": self.token}

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        q = params or {}
        q.update(self._auth())
        return self.client.get(path, params=q)
