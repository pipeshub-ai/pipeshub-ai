from app.web.deadline import Deadline
from app.web.models import WebResult
from app.web.search._api import KeyedProvider

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
        results = []
        for item in data.get("results", [])[:MAX_RESULTS]:
            highlights = " ".join(str(h) for h in item.get("highlights") or [] if h).strip()
            results.append(WebResult(
                url=item.get("url", ""),
                title=item.get("title", ""),
                snippet=highlights or (item.get("text") or "").strip(),
                source=self.id,
                published=item.get("publishedDate"),
            ))
        return results
