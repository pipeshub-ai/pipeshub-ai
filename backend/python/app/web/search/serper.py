from app.web.deadline import Deadline
from app.web.models import WebResult
from app.web.search._api import KeyedProvider
from app.web.search._parse import collect, web_result

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
        return collect(self.id, data.get("organic", []), MAX_RESULTS, lambda item: web_result(
            self.id, item.get("link"), item.get("title"), item.get("snippet"), item.get("date"),
        ))
