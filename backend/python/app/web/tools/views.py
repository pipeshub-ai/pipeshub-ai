"""The tool card's view of a web search or a fetched page: every result, with a working link.

Same shapes as the MCP tools' views (``app/agents/actions/util/result_view.py``), so the chat draws
them with the table and field list it already has.
"""

from typing import Any
from urllib.parse import urlsplit

from app.agents.actions.util.result_view import MAX_ROWS, MAX_VIEW_BYTES, view_bytes
from app.web.url import display_domain

_MAX_CELL_CHARS = 120
_MAX_URL_CHARS = 2000


def _clip(text: object, limit: int = _MAX_CELL_CHARS) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _link(url: object) -> str | None:
    """``url`` if the card may link to it: http(s), a host, no user-info, not too long."""
    if not isinstance(url, str) or len(url) > _MAX_URL_CHARS:
        return None
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    return url


def search_view(payload: dict[str, Any]) -> dict[str, Any] | None:
    """A table of the results: title, site and (when any result has one) date, each row linked."""
    results = [r for r in payload.get("web_results") or [] if isinstance(r, dict)]
    if not results:
        return None
    dated = any(r.get("published") for r in results)
    rows = []
    for result in results[:MAX_ROWS]:
        link = _link(result.get("link"))
        site = result.get("domain") or display_domain(str(result.get("link") or ""))
        cells = [_clip(result.get("title") or site or "Untitled"), _clip(site)]
        if dated:
            cells.append(_clip(result.get("published") or ""))
        rows.append({"cells": cells, **({"url": link} if link else {})})
    view = {
        "kind": "records",
        "columns": ["Title", "Site", *(["Date"] if dated else [])],
        "rows": rows,
        "total": len(results),
    }
    while view["rows"] and view_bytes(view) > MAX_VIEW_BYTES:
        view["rows"] = view["rows"][:-1]
    return view if view["rows"] else None


def page_view(payload: dict[str, Any]) -> dict[str, Any] | None:
    """The page that was read: its title and site, linked."""
    link = _link(payload.get("url"))
    if link is None:
        return None
    site = display_domain(link)
    fields = [{"label": "Page", "value": _clip(payload.get("title") or site)}, {"label": "Site", "value": site}]
    return {"kind": "fields", "fields": fields, "url": link}


def web_view(payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("result_type") == "web_search":
        return search_view(payload)
    if payload.get("result_type") == "url_content":
        return page_view(payload)
    return None
