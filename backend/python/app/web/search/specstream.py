"""SpecStream web search: one GET with a bearer key; the reply lists the search results it read."""

from app.web.deadline import Deadline
from app.web.errors import UpstreamError
from app.web.models import WebResult
from app.web.search._api import KeyedProvider
from app.web.search._parse import collect, field, text, web_result

MAX_RESULTS = 10


class SpecStreamSearch(KeyedProvider):
    id = "specstream"
    name = "SpecStream"

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        data = await self.get_json(
            "https://api.specstream.ai/v1/web-search",
            headers={"Authorization": f"Bearer {self.api_key()}", "Accept": "application/json"},
            params={"q": query},
            deadline=deadline,
        )
        # The API wraps every reply as {"data": ..., "error": ...}; a body without the wrapper is read as it is.
        body = data.get("data") or data
        if body is data and data.get("error"):
            message = text(field(data.get("error")).get("message")) or "request failed"
            raise UpstreamError(f"SpecStream error: {message}", source=self.id)
        return collect(self.id, body.get("sources", []), MAX_RESULTS, lambda item: web_result(
            self.id, item.get("url"), item.get("title"), item.get("snippet"), item.get("published"),
        ))
