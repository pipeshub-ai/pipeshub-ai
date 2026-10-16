"""A SearXNG metasearch server the admin runs (the ``search-searxng`` Compose profile ships one)."""

from typing import Any

import httpx

from app.web.deadline import Deadline
from app.web.endpoint import Endpoint
from app.web.errors import AuthFailed, NotConfigured
from app.web.models import WebResult

MAX_RESULTS = 10
_HTTP_FORBIDDEN = 403


class SearxngSearch:
    id = "searxng"
    name = "SearXNG"

    def __init__(self, configuration: dict[str, Any], http: httpx.AsyncClient) -> None:
        self._endpoint = Endpoint(
            str(configuration.get("baseUrl") or ""), str(configuration.get("apiKey") or ""), source=self.id, name=self.name,
        )
        self.cooldown_key = f"searxng:{self._endpoint.base_url}"

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        try:
            data = await self._endpoint.call("GET", "/search", deadline, params={"q": query, "format": "json"})
        except AuthFailed as e:
            if e.status != _HTTP_FORBIDDEN:
                raise
            # SearXNG answers 403 to format=json unless its settings.yml lists json under search.formats.
            raise NotConfigured("SearXNG refused to answer in JSON: add json to search.formats in its settings.yml", source=self.id) from e
        return [
            WebResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("content", ""),
                source=self.id,
                published=item.get("publishedDate"),
            )
            for item in data.get("results", [])[:MAX_RESULTS]
            if isinstance(item, dict)
        ]
