"""``fetch_url``'s side of WP83-FU-3: a page from an address only the organization reaches is not public.

The page fetch is an unauthenticated GET that refuses every private address (FG2), so its pages are public web content
(``WEB_RULE``) unless the org's ``PublicReachability`` says the page's host is org-only: by name, or because it
resolves into a range the org declared. Then the result is claimed ``unknown`` (``ProvenanceStamp``'s review, before
anything decides or caches it), and a later send outside the organization asks as after any private read.
``repository_fetch`` asks the same classifier for its clones (``app.harness.repositories``).

An org that declares nothing costs nothing here: no lookup is made unless the org lists address ranges.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import socket
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from harness_core.logs import HarnessLog
from harness_core.provenance.types import ResultProvenance
from harness_core.security.reachability import UNREADABLE, PublicReachability

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from pydantic import JsonValue

    from harness_core.effects.types import ToolExecution

# The page fetch under the names a run's port and the Tool Gateway call it by (``tool_sources.WEB_TOOLS``).
PAGE_FETCH_NAMES: Final = frozenset({"dynamic__fetch_url", "fetch_url"})
# What a page from an org-only address carries: content nobody outside can be said to read, written by its author.
ORG_ONLY_PAGE: Final = ResultProvenance("unknown", "external", ())
_URL: Final = re.compile(r'"url"\s*:\s*"([^"\\]{1,2048})"')
_LOOKUP_S: Final = 5.0
_LOG = HarnessLog.named("app.harness.web_reachability")


async def _addresses(host: str) -> list[str]:
    infos = await asyncio.wait_for(
        asyncio.to_thread(socket.getaddrinfo, host, None, type=socket.SOCK_STREAM), _LOOKUP_S
    )
    return sorted({str(info[4][0]) for info in infos})


def page_url(content: JsonValue) -> str | None:
    """The fetched page's URL from ``fetch_url``'s answer (its JSON, as the port holds it); ``None`` for a failed
    fetch or an answer that names none."""
    document = content
    if isinstance(content, str):
        try:
            document = json.loads(content)
        except ValueError:
            found = _URL.search(content)
            return found[1] if found else None
    if not isinstance(document, dict) or document.get("ok") is not True:
        return None
    url = document.get("url")
    return url if isinstance(url, str) else None


@dataclass
class PageReview:
    """``ResultReview`` for a run: ``reachability`` reads the org's classifier at each call."""

    reachability: Callable[[], Awaitable[PublicReachability]]
    resolve: Callable[[str], Awaitable[Sequence[str]]] = _addresses
    log: HarnessLog = field(default_factory=lambda: _LOG)

    async def __call__(self, name: str, execution: ToolExecution) -> ToolExecution:
        if name not in PAGE_FETCH_NAMES or execution.status != "ok":
            return execution
        url = page_url(execution.content)
        host = urlsplit(url).hostname if url else None
        if host is None:
            return execution
        reach = await self._reach()
        addresses: Sequence[str] = ()
        if reach.declares_ranges and not reach.name_org_only(host):
            try:
                addresses = await self.resolve(host)
            except (OSError, TimeoutError, UnicodeError) as exc:
                # The page's address cannot be checked against the org's ranges: not cleared (fail closed).
                self.log.info("web.reachability_unresolved", errorType=type(exc).__name__)
        if not reach.org_only(host, addresses):
            return execution
        self.log.info("web.page_org_only")
        return dataclasses.replace(execution, provenance=ORG_ONLY_PAGE)

    async def _reach(self) -> PublicReachability:
        try:
            return await self.reachability()
        except Exception as exc:  # the org's addresses cannot be read: nothing is cleared (fail closed)
            self.log.warning("web.reachability_unread", errorType=type(exc).__name__)
            return UNREADABLE


__all__ = ["ORG_ONLY_PAGE", "PAGE_FETCH_NAMES", "PageReview", "page_url"]
