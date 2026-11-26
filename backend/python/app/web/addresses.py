"""The web addresses a run was given, and so may open after it read private content.

A page fetch's address is chosen by the model, and after a private read it could carry what the run read to any
public host (``https://example.net/?q=<the record>``). Web search has no such way out: its query goes only to the
configured search provider. So once the flow rule would hold a send, a fetch runs only for an address the run was
given, and every other one is refused (``harness_core.effects.flow.unvouched_address``). Given means it reached the
run from outside the model, verbatim:

* a web search result or a page the run fetched, and a link found in one (``note_result``): a page's links are its
  author's, chosen before the run read anything private, so following one carries nothing the run read. On a durable
  run the session's journal keeps them (the provenance ledger's web pages and the links journaled beside them,
  ``bind_session``), so a resumed or later turn keeps them too. At most ``MAX_PAGE_LINKS`` links are kept per page or
  search and ``MAX_SESSION_LINKS`` per session, first seen first; a link past either has to be searched for;
* a record's or connector item's own link the platform showed the model (the citation ref mapper);
* the person's own words: the message, their earlier messages in the conversation, a message they sent while the run
  worked, and the text of files they attached (``note_user_text``). A child run's goal is its parent model's, never
  the person's.

Links inside a record or a connector item do not count, only its own link: the run can write such content itself (a
note in the person's own space runs without asking) and read it back. Matching is exact after a normalisation that
gives the model nothing to choose: scheme and host case, a default port, a fragment and one trailing slash. Path and
query are compared as written. A redirect needs no check of its own: the address the model gave was vouched for
exactly, so the hop's target is the server's choice, not the model's.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

from app.utils.citations import extract_tiny_ref

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from harness_core.effects.types import ToolEffect
    from harness_core.kernel.core.messages import ToolCall

__all__ = [
    "MAX_PAGE_LINKS",
    "MAX_SESSION_LINKS",
    "WEB_ADDRESSES_KEY",
    "RunAddresses",
    "WebAddresses",
    "address_key",
    "addresses_in",
    "user_turns",
    "web_addresses",
]

WEB_ADDRESSES_KEY: Final = "web_addresses"
_URL_ARG: Final = "url"
_DEFAULT_PORTS: Final = {"http": 80, "https": 443}
# An address in free text: a scheme-led URL, or a bare host with a path (``docs.example.com/guide``) a person typed.
_SCHEMED: Final = re.compile(r"https?://[^\s<>\"'`\]\[{}|\\^]+", re.IGNORECASE)
_BARE: Final = re.compile(
    r"(?<![\w@/.:-])((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}(?::\d{1,5})?(?:/[^\s<>\"'`\]\[{}|\\^]*)?)",
    re.IGNORECASE,
)
_TRAILING: Final = ".,;:!?*_~"
# A page's links worth following are its first few hundred (navigation, references); a session that opened many pages
# keeps the first links it met, so the set and its journal stay small.
MAX_PAGE_LINKS: Final = 200
MAX_SESSION_LINKS: Final = 5_000
_MAX_TEXT_CHARS: Final = 2_000_000


def address_key(url: str) -> str | None:
    """``url`` as addresses are compared: ``None`` for one that is not an http(s) address with a host."""
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower().rstrip(".")
    if scheme not in _DEFAULT_PORTS or not host or parts.username is not None or parts.password is not None:
        return None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None and port != _DEFAULT_PORTS[scheme]:
        netloc = f"{netloc}:{port}"
    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return f"{scheme}://{netloc}{path}" + (f"?{parts.query}" if parts.query else "")


def _trimmed(found: str) -> str:
    """A URL as it ends in prose: without closing punctuation, or a closing bracket it does not open."""
    while found:
        if found[-1] in _TRAILING:
            found = found[:-1]
        elif found[-1] == ")" and found.count("(") < found.count(")"):
            found = found[:-1]
        else:
            break
    return found


def links_in(texts: Iterable[object], *, limit: int = MAX_PAGE_LINKS) -> list[str]:
    """The scheme-led URLs in ``texts``, as written, first seen first, at most ``limit`` distinct addresses."""
    found: dict[str, str] = {}
    for text in texts:
        if not isinstance(text, str):
            continue
        for match in _SCHEMED.finditer(text[:_MAX_TEXT_CHARS]):
            url = _trimmed(match.group(0))
            key = address_key(url)
            if key is not None and key not in found:
                found[key] = url
                if len(found) >= limit:
                    return list(found.values())
    return list(found.values())


def addresses_in(text: str) -> set[str]:
    """The address keys ``text`` names, a bare host with a path under both schemes."""
    text = text[:_MAX_TEXT_CHARS]
    keys: set[str] = set()
    for match in _SCHEMED.finditer(text):
        key = address_key(_trimmed(match.group(0)))
        if key is not None:
            keys.add(key)
    for match in _BARE.finditer(_SCHEMED.sub(" ", text)):
        bare = _trimmed(match.group(1))
        for scheme in ("https", "http"):
            key = address_key(f"{scheme}://{bare}")
            if key is not None:
                keys.add(key)
    return keys


class WebAddresses:
    """One run's given addresses (``tool_state[WEB_ADDRESSES_KEY]``), by ``address_key``."""

    def __init__(self) -> None:
        self._keys: set[str] = set()
        self._links: set[str] = set()
        self._session: Callable[[], Iterable[str]] | None = None
        # The links each result gave, in order, for the run's journal (``app.harness.citations``: ``webLinks``).
        self.recorded: list[dict[str, Any]] = []

    def add(self, urls: Iterable[object]) -> None:
        for url in urls:
            key = address_key(url) if isinstance(url, str) else None
            if key is not None:
                self._keys.add(key)

    def note_user_text(self, text: object) -> None:
        """Text the person wrote or attached."""
        if isinstance(text, str) and text:
            self._keys.update(addresses_in(text))

    def note_result(self, payload: Mapping[str, Any]) -> None:
        """A web tool's successful payload: each search result's link and the links in its snippet, a fetched page's
        address and the links in its text."""
        page = payload.get(_URL_ARG)
        self.add([page])
        texts: list[object] = []
        results = payload.get("web_results")
        for item in results if isinstance(results, list) else []:
            if isinstance(item, dict):
                self.add([item.get("link"), item.get(_URL_ARG)])
                texts.append(item.get("snippet"))
        blocks = payload.get("blocks")
        if isinstance(blocks, list):
            texts.extend(block.get("content") for block in blocks if isinstance(block, dict))
        self.note_links(page if isinstance(page, str) else "", links_in(texts))

    def note_links(self, page: str, links: Iterable[str]) -> None:
        """Links a page or a search gave, kept while the session holds fewer than ``MAX_SESSION_LINKS``."""
        kept: list[str] = []
        for url in links:
            key = address_key(url)
            if key is None or key in self._links:
                continue
            if len(self._links) >= MAX_SESSION_LINKS:
                break
            self._links.add(key)
            kept.append(url)
        if kept:
            self.recorded.append({"page": page, "links": kept})

    def bind_session(self, given: Callable[[], Iterable[str]]) -> None:
        """What the session's journal recorded as given (its web pages and their links), read at each check."""
        self._session = given

    def holds(self, key: str) -> bool:
        if key in self._keys or key in self._links:
            return True
        if self._session is None:
            return False
        return any(address_key(url) == key for url in self._session())


def web_addresses(tool_state: dict[str, Any]) -> WebAddresses:
    """The run's given addresses, made on first use."""
    found = tool_state.get(WEB_ADDRESSES_KEY)
    if not isinstance(found, WebAddresses):
        found = WebAddresses()
        tool_state[WEB_ADDRESSES_KEY] = found
    return found


class RunAddresses:
    """``VouchesAddress`` over one run's tool state: the addresses it was given, the citation ref mapper's links, and
    ``user_text``, what the person wrote (the run's message and their earlier ones; none for a child run)."""

    def __init__(self, tool_state: dict[str, Any], *, user_text: Iterable[object] = ()) -> None:
        self._state = tool_state
        self._user_keys: set[str] = set()
        for text in user_text:
            if isinstance(text, str) and text:
                self._user_keys.update(addresses_in(text))

    def vouched(self, call: ToolCall, effect: ToolEffect) -> bool:
        del effect
        return self.vouches(call.arguments)

    def vouches(self, arguments: Mapping[str, object]) -> bool:
        url = arguments.get(_URL_ARG)
        if not isinstance(url, str) or not url:
            return False
        mapper = self._state.get("citation_ref_mapper")
        ref = extract_tiny_ref(url.strip())
        if ref is not None:
            # A ref stands for a link the platform showed the model; one it never minted opens nothing.
            return mapper is not None and ref in mapper.ref_to_url
        key = address_key(url)
        if key is None:
            return False
        if key in self._user_keys or web_addresses(self._state).holds(key):
            return True
        if mapper is not None:
            return any(address_key(known) == key for known in mapper.url_to_ref)
        return False


def user_turns(previous_conversations: Iterable[Mapping[str, object]]) -> list[object]:
    """The person's own messages in a conversation's history (``role: user_query``)."""
    return [turn.get("content") for turn in previous_conversations if turn.get("role") == "user_query"]

