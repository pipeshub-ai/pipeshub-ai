"""The check a provider passes before an admin can save it."""

from typing import Any

from app.web.deadline import Deadline
from app.web.errors import UnknownProvider
from app.web.fetch import FETCHERS
from app.web.http_client import shared_http_client
from app.web.search import PROVIDERS

# A page any working fetcher can read.
_PROBE_PAGE = "https://example.com/"


def provider_name(type_id: str | None) -> str:
    if type_id in PROVIDERS:
        return PROVIDERS[type_id].name
    if type_id in FETCHERS:
        return getattr(FETCHERS[type_id], "name", type_id)
    return type_id or "The web search provider"


async def check_provider(type_id: str, configuration: dict[str, Any], deadline: Deadline) -> None:
    """One real search, or one real page read for a provider that only reads pages, after the
    provider's own probe when it has one. Raises a ``WebError``."""
    if type_id in PROVIDERS:
        await PROVIDERS[type_id].build(configuration, shared_http_client()).search("health check test", deadline)
        return
    if type_id not in FETCHERS:
        raise UnknownProvider(f"Unknown web search provider: {type_id}", source=type_id)
    fetcher = FETCHERS[type_id](configuration)
    probe = getattr(fetcher, "probe", None)
    if probe is not None:
        await probe(deadline)
    await fetcher.fetch(_PROBE_PAGE, deadline)
