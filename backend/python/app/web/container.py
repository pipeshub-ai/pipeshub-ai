"""Builds the web services for a run from the org's stored settings."""

from dataclasses import dataclass
from typing import Any

from app.web.budget import WebBudget
from app.web.chain import Cooldowns
from app.web.deadline import Deadline
from app.web.errors import NotConfigured, UnknownProvider
from app.web.fetch import FETCHERS
from app.web.fetch.browser import BrowserRenderer
from app.web.fetch.builtin import BuiltinFetcher
from app.web.http_client import shared_http_client
from app.web.models import WebResult
from app.web.ports import ContentFetcher, SearchProvider, WebAccessGate
from app.web.search import FREE_PROVIDERS, PROVIDERS
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


class NoSearchProvider:
    """Stands in when nothing that searches is configured, so a search reports ``not_configured``
    instead of reaching an engine the admin never chose."""

    id = "none"
    cooldown_key = "none"

    async def search(self, query: str, deadline: Deadline) -> list[WebResult]:
        raise NotConfigured("No web search provider is configured", source=self.id, reason="no_provider")


def _others(selection: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in selection.get("providers") or [] if isinstance(p, dict)]


def search_providers(selection: dict[str, Any]) -> list[SearchProvider]:
    """The chosen provider, then the other stored providers that search. A free provider
    (``duckduckgo``, or ``ddgs`` an agent names) runs both free engines, ``ddgs`` first; they run
    only when the admin or an agent chose one of them."""
    http = shared_http_client()
    type_id = selection.get("provider") or ""
    # A provider that only reads pages (an agent may name one) leaves the search to the others.
    page_only = type_id in FETCHERS and type_id not in PROVIDERS  # Crawl4AI; Firecrawl does both
    if type_id and not page_only and type_id not in PROVIDERS:
        return [UnknownSearchProvider(type_id)]
    wanted = [(type_id, selection.get("configuration") or {})] if type_id and not page_only else []
    wanted += [
        (p["provider"], p.get("configuration") or {})
        for p in _others(selection)
        if p.get("provider") in PROVIDERS and p.get("provider") != type_id
    ]
    built: list[SearchProvider] = []
    free_seen: set[str] = set()
    for provider, configuration in wanted:
        if provider not in FREE_PROVIDERS:
            built.append(PROVIDERS[provider].build(configuration, http))
            continue
        built.extend(
            PROVIDERS[free_id].build({}, http) for free_id in FREE_PROVIDERS if free_id not in free_seen
        )
        free_seen.update(FREE_PROVIDERS)
    return built or [NoSearchProvider()]


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
