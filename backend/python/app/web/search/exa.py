from app.web.deadline import Deadline
from app.web.models import WebResult
from app.web.search._api import KeyedProvider
from app.web.search._parse import collect, text, web_result

MAX_RESULTS = 10


class ExaSearch(KeyedProvider):
    id = "exa"
    name = "Exa"

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        data = await self.post_json(
            "https://api.exa.ai/search",
            headers={"Authorization": f"Bearer {self.api_key()}", "Content-Type": "application/json"},
            # Summaries are billed per result and were only a fallback for missing highlights.
            payload={"query": query, "numResults": MAX_RESULTS, "contents": {"text": True, "highlights": True}},
            deadline=deadline,
        )
        return collect(self.id, data.get("results", []), MAX_RESULTS, self._result)

    def _result(self, item: dict) -> WebResult:
        highlights = item.get("highlights")
        joined = " ".join(text(h) for h in highlights if text(h)) if isinstance(highlights, list) else ""
        return web_result(self.id, item.get("url"), item.get("title"), joined or text(item.get("text")), item.get("publishedDate"))
