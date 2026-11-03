"""Builds the web services for a run from the org's stored settings."""

from dataclasses import dataclass
from typing import Any

from app.web.budget import WebBudget
from app.web.chain import Cooldowns
from app.web.config import DEFAULT_PROVIDER
from app.web.deadline import Deadline
from app.web.errors import UnknownProvider
from app.web.fetch import FETCHERS
from app.web.fetch.browser import BrowserRenderer
from app.web.fetch.builtin import BuiltinFetcher
from app.web.http_client import shared_http_client
from app.web.models import WebResult
from app.web.ports import ContentFetcher, SearchProvider, WebAccessGate
from app.web.search import FREE_PROVIDERS, PROVIDERS, spec_for
from app.web.services import WebFetchService, WebSearchService

# What each source failed with lately is shared by every run in the process.
COOLDOWNS = Cooldowns()


@dataclass(frozen=True)
class WebServices:
    search: WebSearchService
    fetch: WebFetchService


class UnknownSearchProvider:
    """Stands in for a stored provider this build doesn't have, so every search reports it
    instead of quietly using another provider."""

    def __init__(self, type_id: str) -> None:
        self.id = type_id
        self.cooldown_key = f"unknown:{type_id}"

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        raise UnknownProvider(f"Unknown web search provider: {self.id}", source=self.id)


def _others(selection: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in selection.get("providers") or [] if isinstance(p, dict)]


def search_providers(selection: dict[str, Any]) -> list[SearchProvider]:
    """The chosen provider, then the other stored providers that search, then the free ones."""
    http = shared_http_client()
    type_id = selection.get("provider") or DEFAULT_PROVIDER
    # A provider that only reads pages (an agent may name one) leaves the search to the others.
    page_only = type_id in FETCHERS and type_id not in PROVIDERS  # Crawl4AI; Firecrawl does both
    searches = type_id != DEFAULT_PROVIDER and not page_only
    if searches and type_id not in PROVIDERS:
        return [UnknownSearchProvider(type_id)]
    chosen = [spec_for(type_id).build(selection.get("configuration") or {}, http)] if searches else []
    others = [
        PROVIDERS[p["provider"]].build(p.get("configuration") or {}, http)
        for p in _others(selection)
        if p.get("provider") in PROVIDERS and p.get("provider") not in (type_id, DEFAULT_PROVIDER)
    ]
    # A free engine the admin also stored as a provider is tried once, in its stored place.
    taken = {*([type_id] if searches else []), *(p.get("provider") for p in _others(selection))}
    free = [PROVIDERS[free_id].build({}, http) for free_id in FREE_PROVIDERS if free_id not in taken]
    return [*chosen, *others, *free]


def fetchers(selection: dict[str, Any]) -> list[ContentFetcher]:
    """The built-in fetcher, then the stored providers that read pages (Firecrawl, Crawl4AI), which
    render JavaScript and get past more bot checks but cost a request to another server."""
    stored = [{"provider": selection.get("provider"), "configuration": selection.get("configuration")}, *_others(selection)]
    remote = [FETCHERS[p["provider"]](p.get("configuration") or {}) for p in stored if p.get("provider") in FETCHERS]
    return [BuiltinFetcher(BrowserRenderer() if BrowserRenderer.available() else None), *remote]


def build_web_services(selection: dict[str, Any], gate: WebAccessGate, budget: WebBudget | None = None) -> WebServices:
    """``budget`` is shared by both services: one answer's searches and page reads."""
    return WebServices(
        search=WebSearchService(search_providers(selection), gate, cooldowns=COOLDOWNS, budget=budget),
        fetch=WebFetchService(fetchers(selection), gate, cooldowns=COOLDOWNS, budget=budget),
    )
