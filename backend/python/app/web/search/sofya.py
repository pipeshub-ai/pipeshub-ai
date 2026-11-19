"""Sofya web search (https://sofya.co/docs)."""

from app.web.deadline import Deadline
from app.web.models import WebResult
from app.web.search._api import KeyedProvider
from app.web.search._parse import collect, text, web_result

MAX_RESULTS = 10
SEARCH_URL = "https://sofya.co/v1/search"


class SofyaSearch(KeyedProvider):
    id = "sofya"
    name = "Sofya"

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        data = await self.post_json(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {self.api_key()}", "Content-Type": "application/json"},
            # "snippets" does not read each result page; the agent's fetch tool reads the pages it needs.
            payload={"query": query, "search_depth": "snippets", "max_results": MAX_RESULTS},
            deadline=deadline,
        )
        return collect(self.id, data.get("results"), MAX_RESULTS, self._result)

    def _result(self, item: dict) -> WebResult:
        snippet = text(item.get("description")) or text(item.get("content"))
        return web_result(self.id, item.get("url"), item.get("title"), snippet, item.get("published_date"))
