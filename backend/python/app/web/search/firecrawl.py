from typing import Any

import httpx

from app.web.deadline import Deadline
from app.web.firecrawl import NAME, firecrawl_endpoint
from app.web.models import WebResult

MAX_RESULTS = 10


class FirecrawlSearch:
    id = "firecrawl"
    name = NAME

    def __init__(self, configuration: dict[str, Any], http: httpx.AsyncClient) -> None:
        self._endpoint = firecrawl_endpoint(configuration, self.id)
        self.cooldown_key = f"firecrawl:{self._endpoint.base_url}"

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        data = await self._endpoint.call("POST", "/v2/search", deadline, json={"query": query, "limit": MAX_RESULTS})
        found = data.get("data")
        # v2 groups results by kind ({"web": [...]}); v1 servers answer a plain list.
        items = found.get("web", []) if isinstance(found, dict) else found if isinstance(found, list) else []
        return [
            WebResult(url=item.get("url", ""), title=item.get("title", ""), snippet=item.get("description", ""), source=self.id)
            for item in items[:MAX_RESULTS]
            if isinstance(item, dict)
        ]
