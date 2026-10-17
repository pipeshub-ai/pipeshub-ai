"""A SearXNG metasearch server the admin runs (the ``search-searxng`` Compose profile ships one)."""

from typing import Any

import httpx

from app.web.deadline import Deadline
from app.web.endpoint import Endpoint
from app.web.errors import AuthFailed, NotConfigured
from app.web.models import WebResult
from app.web.search._parse import collect, web_result

MAX_RESULTS = 10
_HTTP_FORBIDDEN = 403


class SearxngSearch:
    id = "searxng"
    name = "SearXNG"

    def __init__(self, configuration: dict[str, Any], http: httpx.AsyncClient) -> None:
        self._endpoint = Endpoint(
            str(configuration.get("baseUrl") or ""), str(configuration.get("apiKey") or ""), source=self.id, name=self.name,
        )
        self.cooldown_key = self._endpoint.cooldown_key("searxng")

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        try:
            data = await self._endpoint.call("GET", "/search", deadline, params={"q": query, "format": "json"})
        except AuthFailed as e:
            if e.status != _HTTP_FORBIDDEN:
                raise
            # SearXNG answers 403 to format=json unless its settings.yml lists json under search.formats.
            raise NotConfigured(
                "SearXNG refused to answer in JSON: add json to search.formats in its settings.yml",
                source=self.id,
                reason="searxng_json",
            ) from e
        return collect(self.id, data.get("results", []), MAX_RESULTS, lambda item: web_result(
            self.id, item.get("url"), item.get("title"), item.get("content"), item.get("publishedDate"),
        ))
