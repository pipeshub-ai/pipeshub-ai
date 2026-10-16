from app.web.deadline import Deadline
from app.web.models import WebResult
from app.web.search._api import KeyedProvider

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
        return [
            WebResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("content", ""),
                source=self.id,
                published=item.get("published_date"),
            )
            for item in data.get("results", [])[:MAX_RESULTS]
        ]
