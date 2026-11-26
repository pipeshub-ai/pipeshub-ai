"""In-memory adapters of the gateway ports, for tests and for hosts that hold keys in process."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from harness_core.gateway.types import Citation

if TYPE_CHECKING:
    from harness_core.gateway.ports import PublishedKeys
    from harness_core.journal.types import NewEvent

REF = re.compile(r"\bref(\d+)\b")


def refs_in(text: str) -> tuple[str, ...]:
    """The ``refN`` a text mentions, in first-seen order, each once."""
    seen: dict[str, None] = {}
    for match in REF.finditer(text):
        seen.setdefault(f"ref{match.group(1)}", None)
    return tuple(seen)


class StaticGatewayKeySource:
    """Keys held in process; ``publish`` replaces them (a rotation)."""

    def __init__(self, keys: PublishedKeys | None = None) -> None:
        self._keys = keys
        self.refreshes = 0

    def publish(self, keys: PublishedKeys | None) -> None:
        self._keys = keys

    async def keys(self, *, refresh: bool = False) -> PublishedKeys | None:
        if refresh:
            self.refreshes += 1
        return self._keys


class InMemoryCitationSink:
    """A run's ref mapper in memory: ``ref_for`` is what a knowledge tool does when it labels a block."""

    def __init__(self) -> None:
        self._url_to_ref: dict[str, str] = {}
        self._ref_to_url: dict[str, str] = {}
        self._reported = 0
        self.recorded: list[tuple[str, tuple[Citation, ...]]] = []

    def ref_for(self, url: str) -> str:
        ref = self._url_to_ref.get(url)
        if ref is None:
            ref = f"ref{len(self._url_to_ref) + 1}"
            self._url_to_ref[url] = ref
            self._ref_to_url[ref] = url
        return ref

    def cite(self, text: str) -> tuple[Citation, ...]:
        return tuple(Citation(ref, self._ref_to_url[ref]) for ref in refs_in(text) if ref in self._ref_to_url)

    def record(self, invocation_id: str) -> tuple[NewEvent, ...]:
        added = list(self._url_to_ref.items())[self._reported :]
        if added:
            self._reported += len(added)
            self.recorded.append((invocation_id, tuple(Citation(ref, url) for url, ref in added)))
        return ()


__all__ = ["REF", "InMemoryCitationSink", "StaticGatewayKeySource", "refs_in"]
