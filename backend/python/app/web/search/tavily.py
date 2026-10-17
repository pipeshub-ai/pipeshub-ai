from app.web.deadline import Deadline
from app.web.models import WebResult
from app.web.search._api import KeyedProvider
from app.web.search._parse import collect, web_result

MAX_RESULTS = 10


class TavilySearch(KeyedProvider):
    id = "tavily"
    name = "Tavily"

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        data = await self.post_json(
            "https://api.tavily.com/search",
            headers={"Content-Type": "application/json"},
            # "basic" costs one credit per search, "advanced" two; snippets are enough here.
            payload={"api_key": self.api_key(), "query": query, "max_results": MAX_RESULTS, "search_depth": "basic"},
            deadline=deadline,
        )
        return collect(self.id, data.get("results", []), MAX_RESULTS, lambda item: web_result(
            self.id, item.get("url"), item.get("title"), item.get("content"), item.get("published_date"),
        ))
