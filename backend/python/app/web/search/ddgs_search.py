"""The ``ddgs`` metasearch library: free, no key, spreads each query over several engines, so one
engine blocking this server does not end the search."""

import asyncio
import math
from typing import Any

from ddgs import DDGS
from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

from app.web.deadline import Deadline
from app.web.errors import RateLimited, TimedOut, UpstreamError
from app.web.models import WebResult
from app.web.search._parse import collect, web_result

MAX_RESULTS = 10
_NO_RESULTS = "No results found."


class DdgsSearch:
    id = "ddgs"
    name = "ddgs"
    cooldown_key = "ddgs"

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        if deadline.expired:
            raise TimedOut("No time left to search", source=self.id)
        timeout_s = max(1, math.floor(deadline.remaining()))
        try:
            async with asyncio.timeout(deadline.remaining()):
                # The library is synchronous; its own timeout bounds the thread after we stop waiting.
                found = await asyncio.to_thread(self._text, query, timeout_s)
        except TimeoutError as e:
            raise TimedOut("The search engines did not answer in time", source=self.id) from e
        except RatelimitException as e:
            raise RateLimited("The search engines are rate limiting this server", source=self.id) from e
        except TimeoutException as e:
            raise TimedOut("The search engines did not answer in time", source=self.id) from e
        except DDGSException as e:
            if str(e) == _NO_RESULTS:
                return []
            raise UpstreamError(f"The search engines failed: {e}", source=self.id) from e
        return collect(self.id, found, MAX_RESULTS, lambda item: web_result(
            self.id, item.get("href"), item.get("title"), item.get("body"),
        ))

    @staticmethod
    def _text(query: str, timeout_s: int) -> list[dict[str, Any]]:
        return DDGS(timeout=timeout_s).text(query, max_results=MAX_RESULTS, backend="auto")
