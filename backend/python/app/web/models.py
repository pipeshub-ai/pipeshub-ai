from dataclasses import dataclass

from app.utils.html_to_blocks import TextBlock
from app.web.url import display_domain


@dataclass(frozen=True)
class WebResult:
    """One search hit, whichever provider found it."""

    url: str
    title: str
    snippet: str
    source: str  # id of the provider that returned it
    published: str | None = None  # as the provider gave it

    @property
    def domain(self) -> str:
        return display_domain(self.url)


@dataclass(frozen=True)
class WebPage:
    """The readable text of one fetched page."""

    url: str  # after redirects
    title: str
    blocks: tuple[TextBlock, ...]
    fetched_by: str


@dataclass(frozen=True)
class CallerContext:
    """Who a web request is made for."""

    org_id: str
    user_id: str
    conversation_id: str | None = None


@dataclass(frozen=True)
class Attempt:
    """One source tried by a fallback chain; ``error_type`` is None when it answered."""

    source: str
    error_type: str | None
    seconds: float
