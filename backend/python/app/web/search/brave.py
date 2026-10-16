from app.web.deadline import Deadline
from app.web.models import WebResult
from app.web.search._api import KeyedProvider

MAX_RESULTS = 10


class BraveSearch(KeyedProvider):
    id = "brave"
    name = "Brave Search"

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        data = await self.get_json(
            "https://api.search.brave.com/res/v1/web/search",
            headers={"X-Subscription-Token": self.api_key(), "Accept": "application/json"},
            params={"q": query, "count": MAX_RESULTS},
            deadline=deadline,
        )
        web = data.get("web") if isinstance(data.get("web"), dict) else {}
        return [
            WebResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=item.get("description", ""),
                source=self.id,
                published=item.get("page_age") or item.get("age"),
            )
            for item in web.get("results", [])[:MAX_RESULTS]
        ]
