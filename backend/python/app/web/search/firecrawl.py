from typing import Any

import httpx

from app.web.deadline import Deadline
from app.web.firecrawl import NAME, firecrawl_endpoint
from app.web.models import WebResult
from app.web.search._parse import collect, web_result

MAX_RESULTS = 10


class FirecrawlSearch:
    id = "firecrawl"
    name = NAME

    def __init__(self, configuration: dict[str, Any], http: httpx.AsyncClient) -> None:
        self._endpoint = firecrawl_endpoint(configuration, self.id)
        self.cooldown_key = self._endpoint.cooldown_key("firecrawl-search")

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        data = await self._endpoint.call("POST", "/v2/search", deadline, json={"query": query, "limit": MAX_RESULTS})
        found = data.get("data")
        # v2 groups results by kind ({"web": [...]}); v1 servers answer a plain list.
        listed = found.get("web", []) if isinstance(found, dict) else found
        return collect(self.id, listed, MAX_RESULTS, lambda item: web_result(
            self.id, item.get("url"), item.get("title"), item.get("description"),
        ))
