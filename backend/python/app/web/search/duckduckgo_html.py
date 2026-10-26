"""DuckDuckGo's no-JavaScript results page: free, no key, and the first to block a busy server."""

from urllib.parse import parse_qs, unquote, urlencode, urlparse

from bs4 import BeautifulSoup

from app.web.deadline import Deadline
from app.web.errors import Blocked, UpstreamError, error_for_status
from app.web.fetch.http import fetch_public
from app.web.models import WebResult

MAX_RESULTS = 10

_HTTP_OK = 200
_HTTP_ACCEPTED = 202
# DuckDuckGo answers a suspected bot with this page (often as a 202) instead of results.
_BOT_CHECK_MARKERS = ("anomaly-modal", "challenge-form", "bots use duckduckgo too")


def unwrap_result_link(href: str) -> str:
    """The target of a DuckDuckGo result link (``//duckduckgo.com/l/?uddg=<url>``)."""
    if not href:
        return ""
    if "uddg=" in href:
        full = href if href.startswith("http") else f"https:{href}"
        target = parse_qs(urlparse(full).query).get("uddg", [None])[0]
        if target:
            return unquote(target)
    if href.startswith("//"):
        return f"https:{href}"
    return href


def parse_results_page(html: str, source: str) -> list[WebResult]:
    """Results from the page. Raises ``Blocked`` for a bot check, or for any page that is
    neither results nor DuckDuckGo's own "no results" answer."""
    if any(marker in html.lower() for marker in _BOT_CHECK_MARKERS):
        raise Blocked("DuckDuckGo blocked the search as automated traffic", source=source)

    soup = BeautifulSoup(html, "html.parser")
    results: list[WebResult] = []
    for item in soup.select(".web-result"):
        title_tag = item.select_one("a.result__a")
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        url = unwrap_result_link(str(title_tag.get("href", "")))
        if title and url:
            snippet_tag = item.select_one(".result__snippet")
            results.append(WebResult(
                url=url,
                title=title,
                snippet=snippet_tag.get_text(strip=True) if snippet_tag else "",
                source=source,
            ))
        if len(results) >= MAX_RESULTS:
            break

    if not results and not soup.select_one(".no-results"):
        raise Blocked("DuckDuckGo returned a page without results", source=source)
    return results


class DuckDuckGoHtml:
    id = "duckduckgo"
    name = "DuckDuckGo"
    cooldown_key = "duckduckgo"  # a block is against this server's address, whoever asks

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        url = f"https://html.duckduckgo.com/html/?{urlencode({'q': query, 'kl': 'wt-wt'})}"
        response = await fetch_public(url, deadline)
        if response.status == _HTTP_ACCEPTED:
            raise Blocked("DuckDuckGo blocked the search as automated traffic", source=self.id, status=response.status)
        if response.status != _HTTP_OK:
            error = error_for_status(response.status, {}, source=self.name)
            raise error or UpstreamError(f"DuckDuckGo returned HTTP {response.status}", source=self.id)
        return parse_results_page(response.content.decode("utf-8", errors="replace"), self.id)
