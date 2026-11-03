"""Search providers by the type id stored in the org's web search settings.

Adding a provider is one module and one entry in ``PROVIDERS``.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from app.web.errors import UnknownProvider
from app.web.ports import SearchProvider
from app.web.search.brave import BraveSearch
from app.web.search.ddgs_search import DdgsSearch
from app.web.search.duckduckgo_html import DuckDuckGoHtml
from app.web.search.exa import ExaSearch
from app.web.search.firecrawl import FirecrawlSearch
from app.web.search.searxng import SearxngSearch
from app.web.search.serper import SerperSearch
from app.web.search.tavily import TavilySearch


@dataclass(frozen=True)
class ProviderSpec:
    type_id: str
    name: str
    build: Callable[[dict[str, Any], httpx.AsyncClient], SearchProvider]


def _keyless(provider: SearchProvider) -> Callable[[dict[str, Any], httpx.AsyncClient], SearchProvider]:
    return lambda configuration, http: provider


PROVIDERS: dict[str, ProviderSpec] = {
    spec.type_id: spec
    for spec in (
        ProviderSpec("duckduckgo", "DuckDuckGo", _keyless(DuckDuckGoHtml())),
        ProviderSpec("ddgs", "ddgs", _keyless(DdgsSearch())),
        ProviderSpec("serper", SerperSearch.name, SerperSearch),
        ProviderSpec("tavily", TavilySearch.name, TavilySearch),
        ProviderSpec("exa", ExaSearch.name, ExaSearch),
        ProviderSpec("brave", BraveSearch.name, BraveSearch),
        ProviderSpec("searxng", SearxngSearch.name, SearxngSearch),
        ProviderSpec("firecrawl", FirecrawlSearch.name, FirecrawlSearch),
    )
}

# Tried after the configured provider, and alone when none is configured: ddgs first, because it
# spreads a query over several engines; DuckDuckGo's own page last.
FREE_PROVIDERS = ("ddgs", "duckduckgo")


def spec_for(type_id: str) -> ProviderSpec:
    spec = PROVIDERS.get(type_id)
    if spec is None:
        raise UnknownProvider(f"Unknown web search provider: {type_id}", source=type_id)
    return spec
