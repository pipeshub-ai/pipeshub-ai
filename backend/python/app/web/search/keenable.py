"""Keenable web search: the public endpoint needs no key; with one, the keyed endpoint is used."""

from app.web.deadline import Deadline
from app.web.models import WebResult
from app.web.search._api import KeyedProvider
from app.web.search._parse import collect, text, web_result

MAX_RESULTS = 10
SNIPPET_CHARS = 500
PUBLIC_URL = "https://api.keenable.ai/v1/search/public"
KEYED_URL = "https://api.keenable.ai/v1/search"


class KeenableSearch(KeyedProvider):
    id = "keenable"
    name = "Keenable"

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        headers = {"Content-Type": "application/json", "X-Keenable-Title": "PipesHub"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        data = await self.post_json(
            KEYED_URL if self._api_key else PUBLIC_URL,
            headers=headers,
            payload={"query": query, "max_results": MAX_RESULTS},
            deadline=deadline,
        )
        return collect(self.id, data.get("results", []), MAX_RESULTS, self._result)

    def _result(self, item: dict) -> WebResult:
        snippet = text(item.get("description")) or text(item.get("snippet"))[:SNIPPET_CHARS]
        return web_result(self.id, item.get("url"), item.get("title"), snippet)
