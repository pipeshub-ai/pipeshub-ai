"""The interfaces web search and fetch are built from. Each failure is raised as a ``WebError``."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from app.web.deadline import Deadline
from app.web.models import CallerContext, WebPage, WebResult

HopCheck = Callable[[str], Awaitable[bool]]


class Source(Protocol):
    """Anything a fallback chain can try."""

    @property
    def id(self) -> str: ...

    @property
    def cooldown_key(self) -> str:
        """What a failure is remembered under: an engine blocking this server, or one API key."""
        ...


class SearchProvider(Source, Protocol):
    async def search(self, query: str, deadline: Deadline) -> list[WebResult]: ...


class ContentFetcher(Source, Protocol):
    async def fetch(self, url: str, deadline: Deadline, *, allow_hop: HopCheck | None = None) -> WebPage:
        """``allow_hop`` is asked about each redirect target before it is requested."""
        ...


@dataclass(frozen=True)
class AccessRequest:
    action: Literal["search", "fetch"]
    target: str  # the query for a search, the canonical URL for a fetch
    caller: CallerContext


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str = ""


class WebAccessGate(Protocol):
    """Decides whether a search or a fetch may happen. Asked before every search, every fetch
    and every redirect, and about every search result before it is shown."""

    async def check(self, request: AccessRequest) -> AccessDecision: ...
