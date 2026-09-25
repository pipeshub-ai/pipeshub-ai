"""Fakes for behaviour tests of the Web connector.

Everything the connector talks to outside our own code is faked at its edge:

* The websites it crawls are a small aiohttp app served on a Unix socket. Every
  ``aiohttp.ClientSession`` opened during a test is pointed at that socket, so the
  real fetch chain (HEAD size check, GET, redirects, 429/503 backoff) runs over
  real HTTP without touching the network. The optional curl_cffi and cloudscraper
  strategies are switched off, as if those libraries were not installed.
* The headless browser is a stand-in for crawl4ai's ``AsyncWebCrawler`` that
  renders pages from the same fake websites, so ``Crawl4AIFetcher``'s own
  thread, semaphore and dispatcher code still runs.
* Our own services (records database, sync checkpoints, config, the storage
  service and notifications) are in-memory, so a second sync sees what the
  first one wrote.

Waits are taken on a virtual clock: they are recorded, not slept.
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from aiohttp import web
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.connectors.sources.web.connector import WebConnector
    from app.models.entities import Record

STORAGE_HOST = "storage.test"
HEAD_HANGS_UP = -1  # a ``head_status`` meaning the site drops HEAD requests without answering
CONNECTOR_ID = "web-1"
START_URL = "http://site.test/"
_real_sleep = asyncio.sleep


MakeConnector = Callable[..., Awaitable["WebConnector"]]


def html_page(title: str, *links: str, text: str = "", body: str = "") -> bytes:
    anchors = "".join(f'<a href="{href}">{href}</a> ' for href in links)
    return (
        f"<html><head><title>{title}</title></head><body>"
        f"<h1>{title}</h1><p>{text or 'Content of ' + title}</p>{body}<div>{anchors}</div>"
        "</body></html>"
    ).encode()


@dataclass
class Page:
    body: bytes = b""
    status: int = 200
    content_type: str | None = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)
    location: str | None = None
    chunked: bool = False
    # What a browser sees after scripts run; default to ``body`` and ``status``.
    rendered: bytes | None = None
    rendered_status: int | None = None
    # Visible text length before scripts run, as the CSR probe measures it.
    pre_render_text_len: int | None = None
    # Close the connection without answering, like a server that went away.
    hang_up: bool = False
    # Chromium aborts navigating onto this file (net::ERR_ABORTED), and crawl4ai raises.
    browser_aborts: bool = False
    # What a HEAD gets instead of the page's usual answer, e.g. 405 from a site without HEAD.
    head_status: int | None = None


    # Validators: sent with the page, and a matching If-None-Match / If-Modified-Since gets a 304.
    etag: str | None = None
    last_modified: str | None = None


def _key(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    return f"{(parsed.hostname or '').lower()}{path}{'?' + parsed.query if parsed.query else ''}"


class FakeWeb:
    """A few in-memory websites plus the PipesHub storage service.

    Pages are keyed by host + path + query. A page may be a list of ``Page``
    objects, answered one per GET (the last one repeats), which is how
    "fails twice, then works" is staged. Unknown URLs get a 404.
    """

    def __init__(self) -> None:
        self._pages: dict[str, Page | list[Page]] = {}
        self._lock = threading.Lock()
        self.requests: list[tuple[str, str]] = []
        self.not_modified: list[str] = []
        self.browser_visits: list[str] = []
        self.browser_starts = 0
        self.browser_broken = False
        self.storage_docs: dict[str, bytes] = {}
        self.storage_uploads: list[str] = []
        self.storage_buffer_updates: list[str] = []
        self.storage_deletes: list[str] = []
        self.storage_down = False
        self._doc_seq = 0

    def add(self, url: str, page: Page | list[Page]) -> "FakeWeb":
        self._pages[_key(url)] = page
        return self

    def html(self, url: str, title: str, *links: str, text: str = "", **page_kw: object) -> "FakeWeb":
        return self.add(url, Page(body=html_page(title, *links, text=text), **page_kw))

    def redirect(self, url: str, to: str, status: int = 302) -> "FakeWeb":
        return self.add(url, Page(status=status, location=to, content_type=None))

    def remove(self, url: str) -> None:
        self._pages.pop(_key(url), None)

    def gets(self, url: str) -> int:
        return sum(1 for method, u in self.requests if method == "GET" and _key(u) == _key(url))

    def fetched_urls(self) -> set[str]:
        """Pages fetched, leaving out the crawler's robots.txt reads."""
        return {u for method, u in self.requests if method == "GET" and not u.endswith("/robots.txt")}

    def _current(self, url: str, consume: bool) -> Page:
        with self._lock:
            entry = self._pages.get(_key(url))
            if entry is None:
                return Page(status=404, body=b"<html><body>Not found</body></html>")
            if isinstance(entry, list):
                if consume and len(entry) > 1:
                    return entry.pop(0)
                return entry[0]
            return entry

    # -- HTTP side -------------------------------------------------------

    async def handle(self, request: web.Request) -> web.StreamResponse:
        host = (request.host or "").split(":")[0].lower()
        if host == STORAGE_HOST:
            return await self._storage(request)
        url = f"http://{host}{request.path_qs}"
        self.requests.append((request.method, url))
        page = self._current(url, consume=request.method == "GET")
        if request.method == "HEAD" and page.head_status is not None:
            if page.head_status == HEAD_HANGS_UP:
                assert request.transport is not None
                request.transport.abort()
                raise ConnectionResetError("fake site dropped a HEAD")
            return web.Response(status=page.head_status)
        if page.hang_up:
            assert request.transport is not None
            request.transport.abort()
            raise ConnectionResetError("fake site hung up")
        headers = dict(page.headers)
        if page.etag:
            headers["ETag"] = page.etag
        if page.last_modified:
            headers["Last-Modified"] = page.last_modified
        if page.status == 200 and (
            (page.etag and request.headers.get("If-None-Match") == page.etag)
            or (page.last_modified and request.headers.get("If-Modified-Since") == page.last_modified)
        ):
            if request.method == "GET":
                self.not_modified.append(url)
            return web.Response(status=304, headers=headers)
        if page.location:
            headers["Location"] = page.location
        if page.content_type:
            headers["Content-Type"] = page.content_type
        if page.chunked:
            response = web.StreamResponse(status=page.status, headers=headers)
            response.enable_chunked_encoding()
            await response.prepare(request)
            if request.method != "HEAD":
                await response.write(page.body)
            await response.write_eof()
            return response
        return web.Response(status=page.status, body=page.body, headers=headers)

    async def _storage(self, request: web.Request) -> web.StreamResponse:
        if self.storage_down:
            return web.Response(status=500, text="storage unavailable")
        path = request.path
        if request.method == "POST" and path == "/api/v1/document/internal/upload":
            form = await request.post()
            upload = form["file"]
            self._doc_seq += 1
            doc_id = f"doc-{self._doc_seq}"
            self.storage_docs[doc_id] = upload.file.read()  # type: ignore[union-attr]
            self.storage_uploads.append(doc_id)
            return web.json_response({"_id": doc_id})
        if request.method == "PUT" and path.endswith("/buffer"):
            doc_id = path.split("/")[-2]
            form = await request.post()
            self.storage_docs[doc_id] = form["file"].file.read()  # type: ignore[union-attr]
            self.storage_buffer_updates.append(doc_id)
            return web.Response(status=200)
        if request.method == "GET" and path.endswith("/buffer"):
            doc_id = path.split("/")[-2]
            if doc_id not in self.storage_docs:
                return web.Response(status=404)
            return web.Response(body=self.storage_docs[doc_id], content_type="application/octet-stream")
        if request.method == "DELETE" and path.startswith("/api/v1/document/internal/"):
            doc_id = path.rsplit("/", 1)[-1]
            self.storage_deletes.append(doc_id)
            self.storage_docs.pop(doc_id, None)
            return web.Response(status=204)
        if request.method == "GET" and path.endswith("/download"):
            doc_id = path.split("/")[-2]
            return web.json_response({"signedUrl": f"http://{STORAGE_HOST}/signed/{doc_id}"})
        return web.Response(status=404)

    # -- Browser side ----------------------------------------------------

    def render(self, url: str) -> tuple[str, Page]:
        """What a browser ends up showing for ``url``, following redirects."""
        for _ in range(10):
            page = self._current(url, consume=True)
            if page.location and 300 <= page.status < 400:
                url = urljoin(url, page.location)
                continue
            return url, page
        return url, Page(status=508)


def browser_crawler_class(site: FakeWeb) -> type:
    """A stand-in for ``crawl4ai.AsyncWebCrawler`` that renders from ``site``."""

    class FakeBrowserCrawler:
        def __init__(self, *_, **__) -> None:
            self.started = False

        async def start(self) -> None:
            site.browser_starts += 1
            if site.browser_broken:
                raise RuntimeError("BrowserType.launch: Executable doesn't exist")
            self.started = True

        async def close(self) -> None:
            self.started = False

        async def arun(self, url: str, config: object = None, **_: object) -> SimpleNamespace:
            site.browser_visits.append(url)
            final_url, page = site.render(url)
            if page.browser_aborts:
                raise RuntimeError(f"Failed on navigating ACS-GOTO:\nPage.goto: net::ERR_ABORTED at {url}")
            if page.hang_up:
                return SimpleNamespace(url=url, redirected_url=url, html="", success=False, status_code=None,
                                       error_message="net::ERR_EMPTY_RESPONSE", crawl_stats=None,
                                       js_execution_result=None)
            status = page.rendered_status if page.rendered_status is not None else page.status
            if page.rendered is not None:
                html = page.rendered.decode("utf-8", "replace")
            elif page.content_type and "html" not in page.content_type:
                # A browser shows a file in its viewer; the file's bytes never reach the page HTML.
                html = f'<html><body><embed type="{page.content_type}" src="{final_url}"></body></html>'
            else:
                html = page.body.decode("utf-8", "replace")
            text_len = len(BeautifulSoup(html, "html.parser").get_text(strip=True)) if html else 0
            pre = page.pre_render_text_len if page.pre_render_text_len is not None else text_len
            ok = status < 400
            return SimpleNamespace(
                # Like crawl4ai's CrawlResult: ``url`` is what was asked for, ``redirected_url`` where it landed.
                url=url,
                redirected_url=final_url,
                html=html,
                success=ok,
                status_code=status,
                error_message=None if ok else f"Failed on navigating ACS-GOTO: HTTP {status}",
                crawl_stats=None,
                js_execution_result={"success": True, "results": [{"preLen": pre, "postLen": text_len}]},
            )

        async def arun_many(self, urls: list[str], config: object = None, dispatcher: object = None, **_: object) -> list[object]:
            task_results = await dispatcher.run_urls(crawler=self, urls=urls, config=config)  # type: ignore[attr-defined]
            return [r if isinstance(r, BaseException) else r.result for r in task_results]

    return FakeBrowserCrawler


class VirtualClock:
    """Stands in for ``asyncio`` inside the connector and fetch modules.

    ``sleep`` records the delay and advances a virtual clock instead of
    waiting; ``get_event_loop().time()`` reads that clock. Everything else is
    the real ``asyncio``.
    """

    def __init__(self) -> None:
        self.offset = 0.0
        self.sleeps: list[float] = []

    def __getattr__(self, name: str) -> object:
        return getattr(asyncio, name)

    async def sleep(self, delay: float, result: object = None) -> object:
        self.sleeps.append(delay)
        self.offset += max(delay, 0)
        await _real_sleep(0)
        return result

    def get_event_loop(self) -> SimpleNamespace:
        loop = asyncio.get_running_loop()
        return SimpleNamespace(time=lambda: loop.time() + self.offset)


class FakeRecordsDb:
    """In-memory stand-in for ``DataSourceEntitiesProcessor``.

    Records are keyed by external id and copied on write, so a later sync sees
    exactly what an earlier one stored. Only the methods the Web connector
    calls exist; anything else raises ``AttributeError``.
    """

    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.records: dict[str, Record] = {}
        self.new_batches: list[list[Record]] = []
        self.content_updates: list[Record] = []
        self.metadata_updates: list[Record] = []
        self.permission_updates: list[Record] = []
        self.deleted: list[str] = []
        self.record_groups: list[Any] = []
        self.fail_writes = False

    def _store(self, record: Record) -> None:
        existing = self.records.get(record.external_record_id)
        if existing is not None:
            record.id = existing.id
        # Records are upserted by id, so one stored under an older external id is replaced.
        self.records = {k: v for k, v in self.records.items() if v.id != record.id}
        self.records[record.external_record_id] = record.model_copy(deep=True)

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Record | None:
        stored = self.records.get(external_record_id)
        return stored.model_copy(deep=True) if stored is not None else None

    async def on_new_records(self, pairs: list[tuple[Record, list[Any]]]) -> None:
        if self.fail_writes:
            raise RuntimeError("records database unavailable")
        self.new_batches.append([rec for rec, _ in pairs])
        for record, _ in pairs:
            self._store(record)

    async def on_record_content_update(self, record: Record) -> None:
        self.content_updates.append(record)
        self._store(record)

    async def on_record_metadata_update(self, record: Record) -> None:
        self.metadata_updates.append(record)
        self._store(record)

    async def on_updated_record_permissions(self, record: Record, permissions: list[Any]) -> None:
        self.permission_updates.append(record)

    async def on_record_deleted(self, record_id: str, **_: object) -> None:
        self.deleted.append(record_id)
        self.records = {k: v for k, v in self.records.items() if v.id != record_id}

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        self.record_groups.extend(groups)

    async def on_new_app_users(self, users: list[Any]) -> None:
        pass

    async def ensure_team_app_edge(self, connector_id: str) -> None:
        pass

    async def get_user_by_user_id(self, user_id: str) -> SimpleNamespace:
        return SimpleNamespace(id=user_id, email="owner@example.com", source_user_id=user_id,
                               org_id=self.org_id, full_name="Owner", is_active=True, title=None)

    def pages(self) -> dict[str, Record]:
        """Stored records for real pages, keyed by URL (ancestor placeholders left out)."""
        return {r.weburl: r for r in self.records.values() if not r.is_internal}

    def mark_indexed(self) -> None:
        """What the indexing service does once it has processed every record."""
        from app.config.constants.arangodb import ProgressStatus

        for record in self.records.values():
            if record.indexing_status != ProgressStatus.FAILED.value:
                record.indexing_status = ProgressStatus.COMPLETED.value


class FakeCheckpointStore:
    """In-memory sync-point collection behind ``DataStoreProvider.transaction()``."""

    def __init__(self) -> None:
        self.sync_points: dict[str, dict[str, Any]] = {}
        self.writes = 0

    async def get_sync_point(self, key: str, raise_on_error: bool = False) -> dict[str, Any] | None:
        return self.sync_points.get(key)

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.writes += 1
        self.sync_points[key] = dict(data)

    async def delete_sync_point(self, key: str) -> None:
        self.sync_points.pop(key, None)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["FakeCheckpointStore"]:
        yield self


class FakeConfigService:
    """Serves the connector's config document, service endpoints and signing keys."""

    def __init__(self, connector_id: str, sync: dict[str, Any], filters: dict[str, Any] | None = None) -> None:
        self.connector_id = connector_id
        self.sync = sync
        self.filters = filters or {}

    async def get_config(self, path: str, default: object = None, use_cache: bool = True, **_: object) -> object:
        if path == f"/services/connectors/{self.connector_id}/config":
            return {"sync": dict(self.sync), "filters": json.loads(json.dumps(self.filters))}
        if path == "/services/endpoints":
            return {"storage": {"endpoint": f"http://{STORAGE_HOST}"}}
        if path == "/services/secretKeys":
            return {"scopedJwtSecret": "behaviour-test-secret-0123456789abcdef"}
        return default


class RecordingNotifications:
    """Connector notifications are published from background tasks; ``delivered`` lets them run."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def delivered(self) -> list[dict[str, Any]]:
        for _ in range(3):
            await _real_sleep(0)
        return self.sent

    async def publish_notification(self, **kwargs: object) -> None:
        self.sent.append(kwargs)
