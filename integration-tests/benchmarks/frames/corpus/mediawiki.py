"""Minimal MediaWiki Action API client: the revision in force at a snapshot,
and that revision's rendered HTML.

Polite by construction: a descriptive User-Agent (no personal contact data),
`maxlag` on every call, and retries that honour `Retry-After`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import requests
from pydantic import BaseModel, ConfigDict

from benchmarks.frames.errors import CorpusError, TransientHTTPError
from benchmarks.frames.retry import http_retry, raise_for_transient

USER_AGENT = (
    "PipesHub-FRAMES-benchmark/1.0 "
    "(https://github.com/pipeshub-ai/pipeshub-ai; corpus builder)"
)
_MAXLAG_S = 5
_MAXLAG_RETRY_AFTER_S = 5.0


class RevisionRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    title: str
    page_id: int
    revid: int
    timestamp: datetime
    post_snapshot: bool = False


class ParsedPage(BaseModel):
    model_config = ConfigDict(frozen=True)

    revid: int
    title: str
    html: str
    links: tuple[str, ...]


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class MediaWikiClient:
    def __init__(
        self,
        host: str,
        *,
        session: requests.Session | None = None,
        timeout_s: float = 30.0,
        user_agent: str = USER_AGENT,
    ) -> None:
        self.host = host
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._timeout_s = timeout_s

    @property
    def api_url(self) -> str:
        return f"https://{self.host}/w/api.php"

    @http_retry(attempts=5)
    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        query = {**params, "format": "json", "formatversion": 2, "maxlag": _MAXLAG_S}
        resp = raise_for_transient(self._session.get(self.api_url, params=query, timeout=self._timeout_s))
        resp.raise_for_status()
        data = resp.json()
        error = data.get("error")
        if error and error.get("code") == "maxlag":
            raise TransientHTTPError(503, _MAXLAG_RETRY_AFTER_S, self.api_url)
        if error:
            raise CorpusError(f"MediaWiki {self.host}: {error.get('code')}: {error.get('info')}")
        return data

    def _revision(self, title: str, snapshot: datetime, direction: Literal["older", "newer"]) -> RevisionRef | None:
        data = self._get({
            "action": "query", "prop": "revisions", "titles": title, "redirects": 1,
            "rvlimit": 1, "rvstart": _iso(snapshot), "rvdir": direction, "rvprop": "ids|timestamp",
        })
        pages = data.get("query", {}).get("pages") or []
        if not pages or pages[0].get("missing") or pages[0].get("invalid"):
            return None
        page = pages[0]
        revisions = page.get("revisions") or []
        if not revisions:
            return None
        return RevisionRef(
            title=page["title"], page_id=int(page["pageid"]),
            revid=int(revisions[0]["revid"]), timestamp=revisions[0]["timestamp"],
        )

    def revision_at(self, title: str, snapshot: datetime) -> RevisionRef | None:
        """Latest revision at or before `snapshot`, following redirects. Pages
        created after the snapshot fall back to their first revision, flagged."""
        older = self._revision(title, snapshot, "older")
        if older is not None:
            return older
        newer = self._revision(title, snapshot, "newer")
        return newer.model_copy(update={"post_snapshot": True}) if newer else None

    def parse_revision(self, revid: int) -> ParsedPage:
        data = self._get({
            "action": "parse", "oldid": revid, "prop": "text|links",
            "disableeditsection": 1, "disablelimitreport": 1,
        })
        parse = data["parse"]
        links = tuple(
            link["title"] for link in parse.get("links", [])
            if link.get("ns") == 0 and link.get("exists")
        )
        return ParsedPage(revid=revid, title=parse["title"], html=parse["text"], links=links)

    def search_title(self, query: str) -> str | None:
        data = self._get({"action": "query", "list": "search", "srsearch": query, "srlimit": 1})
        hits = data.get("query", {}).get("search") or []
        return hits[0]["title"] if hits else None

    @http_retry(attempts=4)
    def resolve_short_link(self, url: str) -> str:
        resp = raise_for_transient(self._session.head(url, allow_redirects=True, timeout=self._timeout_s))
        return resp.url
