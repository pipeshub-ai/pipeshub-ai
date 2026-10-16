"""Builds the web services for a run from the org's stored settings."""

from dataclasses import dataclass
from typing import Any

from app.web.chain import Cooldowns
from app.web.config import DEFAULT_PROVIDER
from app.web.deadline import Deadline
from app.web.errors import UnknownProvider
from app.web.fetch.builtin import BuiltinFetcher
from app.web.http_client import shared_http_client
from app.web.models import WebResult
from app.web.ports import SearchProvider, WebAccessGate
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


def search_providers(selection: dict[str, Any]) -> list[SearchProvider]:
    """The configured provider, then the free ones."""
    http = shared_http_client()
    type_id = selection.get("provider") or DEFAULT_PROVIDER
    free = [PROVIDERS[free_id].build({}, http) for free_id in FREE_PROVIDERS]
    if type_id == DEFAULT_PROVIDER:
        return free
    if type_id not in PROVIDERS:
        return [UnknownSearchProvider(type_id)]
    return [spec_for(type_id).build(selection.get("configuration") or {}, http), *free]


def build_web_services(selection: dict[str, Any], gate: WebAccessGate) -> WebServices:
    return WebServices(
        search=WebSearchService(search_providers(selection), gate, cooldowns=COOLDOWNS),
        fetch=WebFetchService([BuiltinFetcher()], gate, cooldowns=COOLDOWNS),
    )
