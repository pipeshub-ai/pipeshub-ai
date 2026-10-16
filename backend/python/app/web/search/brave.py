from app.web.deadline import Deadline
from app.web.models import WebResult
from app.web.search._api import KeyedProvider
from app.web.search._parse import collect, field, web_result

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
        return collect(self.id, field(data.get("web")).get("results", []), MAX_RESULTS, lambda item: web_result(
            self.id, item.get("url"), item.get("title"), item.get("description"), item.get("page_age") or item.get("age"),
        ))
