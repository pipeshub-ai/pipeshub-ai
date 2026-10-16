from app.web.deadline import Deadline
from app.web.models import WebResult
from app.web.search._api import KeyedProvider

MAX_RESULTS = 10


class SerperSearch(KeyedProvider):
    id = "serper"
    name = "Serper"

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        data = await self.post_json(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": self.api_key(), "Content-Type": "application/json"},
            payload={"q": query},
            deadline=deadline,
        )
        return [
            WebResult(
                url=item.get("link", ""),
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                source=self.id,
                published=item.get("date"),
            )
            for item in data.get("organic", [])[:MAX_RESULTS]
        ]
