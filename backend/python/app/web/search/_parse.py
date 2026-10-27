"""Reading a search API's JSON without trusting its shape: an answer unlike what the API documents
is that source's failure, so the next source is tried, never an exception or a silent empty list."""

from collections.abc import Callable

from app.web.errors import UpstreamError
from app.web.models import WebResult

_WEB = ("http://", "https://")


def field(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def web_result(source: str, url: object, title: object, snippet: object, published: object = None) -> WebResult:
    return WebResult(url=text(url), title=text(title), snippet=text(snippet), source=source, published=text(published) or None)


def collect(
    source: str, entries: object, limit: int, build: Callable[[dict], WebResult],
) -> list[WebResult]:
    """Results from ``entries``, the list of hits an API answered with. Raises ``UpstreamError``
    when it isn't a list, or when it lists hits of which none has a web link."""
    if not isinstance(entries, list):
        raise UpstreamError(f"{source} answered in an unexpected format", source=source)
    results = [build(entry) for entry in entries if isinstance(entry, dict)][:limit]
    if entries and not any(r.url.lower().startswith(_WEB) for r in results):
        raise UpstreamError(f"{source} answered with no usable results", source=source)
    return results
