# pyright: ignore-file

"""Notion API helper for creating and destroying integration-test resources.

Wraps the SAME ``NotionDataSource`` the connector uses at
``app/connectors/sources/notion/connector.py`` ``init()``, so a drift between the wrapper layer
and the real API surfaces here rather than silently in the connector.

Two endpoints do not go through the wrapper:

- ``POST /v1/pages/{id}/move`` has no generated method (raw httpx).
- File uploads use the wrapper, but the ``send`` step is multipart and therefore issues its own
  httpx request inside ``NotionDataSource.send_file_upload``.

``NotionResponse.success`` only means "no exception was raised" — a 404 still comes back as
``success=True``. Every call here goes through ``_unwrap``, which checks the HTTP status.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Optional

import httpx

from app.sources.client.notion.notion import (  # type: ignore[import-not-found]
    NotionClient,
    NotionTokenConfig,
)
from app.sources.external.notion.notion import NotionDataSource  # type: ignore[import-not-found]
from sample_data import ensure_sample_data_files_root  # type: ignore[import-not-found]
from connectors.notion.constants import (  # type: ignore[import-not-found]
    NOTION_API_VERSION,
    NOTION_MAX_CONCURRENCY,
    NOTION_MAX_RETRIES,
    NOTION_MIN_REQUEST_INTERVAL,
)

logger = logging.getLogger("notion-source-helper")

_NOTION_API_BASE = "https://api.notion.com/v1"

# Notion returns 409 on concurrent edits to the same parent and 429 when the ~3 rps budget is
# exhausted; both are safe to retry. 5xx is retried too — creates are idempotent enough here
# because every retry that actually succeeded would have returned 200.
_RETRY_STATUSES = frozenset({409, 429, 500, 502, 503, 504})


# Matches the Notion connector's own ResiliencePolicy max_delay: an upstream
# asking for far longer would otherwise park the whole IT run on one call.
_MAX_BACKOFF_SECONDS = 60.0


def _retry_after_seconds(data: Any) -> Optional[float]:
    """Read Retry-After (seconds) off a response, capped, if it carries one."""
    headers = getattr(data, "headers", None) or {}
    raw = headers.get("Retry-After") or headers.get("retry-after")
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        return None
    return min(seconds, _MAX_BACKOFF_SECONDS) if seconds > 0 else None


def _require_json_object(data: Any, context: str) -> dict[str, Any]:
    """Parse a 2xx body, insisting it is a JSON object.

    Callers all treat the result as a mapping; a valid-but-non-object body would
    otherwise surface far away as an AttributeError on ``.get``.
    """
    try:
        parsed = data.json()
    except Exception as exc:
        raise NotionSourceError(f"{context} returned a non-JSON body: {exc}") from exc
    if not isinstance(parsed, dict):
        raise NotionSourceError(
            f"{context} returned JSON {type(parsed).__name__}, expected an object"
        )
    return parsed


class NotionSourceError(RuntimeError):
    """Raised when a Notion API create/read operation fails."""


def normalize_notion_id(raw: str) -> str:
    """Return a Notion id in dashed uuid form.

    Ids copied out of a Notion URL are 32 undashed hex chars, while every API response returns
    the dashed form. Anywhere the two are compared as strings (the stale-run sweep matches
    ``parent.page_id`` against the configured root) an unnormalized value silently never
    matches, so env-supplied ids are normalized on the way in.
    """
    cleaned = (raw or "").strip().rsplit("/", 1)[-1].split("?", 1)[0]
    # A URL slug looks like "Page-Title-<32 hex>"; keep the trailing id.
    if "-" in cleaned and len(cleaned.replace("-", "")) > 32:
        cleaned = cleaned.rsplit("-", 1)[-1]
    hex_only = cleaned.replace("-", "")
    if len(hex_only) != 32:
        return cleaned
    return "-".join(
        (hex_only[:8], hex_only[8:12], hex_only[12:16], hex_only[16:20], hex_only[20:])
    )


def sample_file_path(relative_path: str) -> Path:
    """Resolve a file inside the shared ``pipeshub-ai/integration-test`` sample-data repo.

    Binaries are never committed to this repo; ``sample_data`` clones the shared one on demand
    (same source the S3 / GCS / Azure suites upload from).
    """
    path = ensure_sample_data_files_root() / relative_path
    if not path.is_file():
        raise NotionSourceError(
            f"sample file {relative_path!r} not found at {path}. It must exist in the shared "
            "pipeshub-ai/integration-test repo under sample-data/entities/files."
        )
    return path


def find_sample_file_by_suffix(suffix: str) -> Optional[Path]:
    """First sample file with ``suffix`` (e.g. ``.svg``), or None if the shared repo has none.

    Used for fixtures that are nice-to-have rather than required, so the suite degrades to
    skipping one assertion instead of failing when the asset has not been added yet.
    """
    try:
        root = ensure_sample_data_files_root()
    except Exception as e:  # noqa: BLE001 - absence is a skip signal, not a failure
        logger.warning("sample-data repo unavailable: %s", e)
        return None
    return next((p for p in sorted(root.rglob(f"*{suffix}")) if p.is_file()), None)


def rich_text(content: str) -> list[dict[str, Any]]:
    """Minimal Notion rich-text array."""
    return [{"type": "text", "text": {"content": content}}]


def paragraph_block(content: str) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph",
            "paragraph": {"rich_text": rich_text(content)}}


def file_upload_block(block_type: str, file_upload_id: str, name: str) -> dict[str, Any]:
    """Attach an uploaded file as a ``file`` / ``pdf`` / ``audio`` / ``video`` / ``image`` block."""
    payload: dict[str, Any] = {
        "type": "file_upload",
        "file_upload": {"id": file_upload_id},
    }
    # ``name`` is only accepted on file blocks; the connector falls back to the URL basename
    # for the other types.
    if block_type == "file":
        payload["name"] = name
    return {"object": "block", "type": block_type, block_type: payload}


def external_media_block(block_type: str, url: str) -> dict[str, Any]:
    return {"object": "block", "type": block_type,
            block_type: {"type": "external", "external": {"url": url}}}


class NotionSourceHelper:
    """Thin, rate-limited wrapper over ``NotionDataSource`` for test resource lifecycle."""

    def __init__(self, token: str, version: str = NOTION_API_VERSION) -> None:
        self._token = token
        self._client = NotionClient.build_with_config(
            NotionTokenConfig(token=token, version=version)
        )
        self.datasource = NotionDataSource(self._client)
        self._version = version
        self._semaphore = asyncio.Semaphore(NOTION_MAX_CONCURRENCY)
        self._last_request_at = 0.0
        self._pace_lock = asyncio.Lock()

    async def close(self) -> None:
        try:
            await self._client.get_client().close()
        except Exception as e:  # noqa: BLE001 - best-effort cleanup
            logger.warning("NotionSourceHelper.close failed: %s", e)

    # ------------------------------------------------------------------ plumbing

    async def _pace(self) -> None:
        """Space requests out to stay under Notion's ~3 rps budget."""
        async with self._pace_lock:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < NOTION_MIN_REQUEST_INTERVAL:
                await asyncio.sleep(NOTION_MIN_REQUEST_INTERVAL - elapsed)
            self._last_request_at = time.monotonic()

    async def _call(self, method: Any, *args: Any, context: str, **kwargs: Any) -> dict[str, Any]:
        """Invoke a ``NotionDataSource`` method with pacing + retry, and return parsed JSON."""
        last_error = ""
        for attempt in range(NOTION_MAX_RETRIES):
            async with self._semaphore:
                await self._pace()
                resp = await method(*args, **kwargs)

            # A non-2xx response comes back as success=False, so the status only
            # ever lives on .data. Reading it here (rather than inside the success
            # branch, where it is always 2xx) is what makes _RETRY_STATUSES and the
            # Retry-After handling below reachable at all.
            data = getattr(resp, "data", None)
            status = getattr(data, "status", None)

            if resp is None:
                last_error = "no response"
            elif resp.success and data is not None and status is not None and 200 <= status < 300:
                return _require_json_object(data, context)
            elif data is not None and status is not None:
                last_error = f"HTTP {status}: {data.text()[:400]}"
            else:
                last_error = (getattr(resp, "error", None) or "no response")

            if status is not None and status not in _RETRY_STATUSES:
                break

            if attempt < NOTION_MAX_RETRIES - 1:
                # Notion's Retry-After is tens of seconds; a blind exponential
                # curve capped at 8s just burns the retry budget before it lifts.
                backoff = _retry_after_seconds(data) or min(2 ** attempt, 8)
                logger.warning("%s failed (%s); retrying in %ss", context, last_error, backoff)
                await asyncio.sleep(backoff)

        raise NotionSourceError(f"{context} failed: {last_error}")

    async def _raw(
        self, method: str, path: str, *, json_body: Optional[dict[str, Any]] = None, context: str
    ) -> dict[str, Any]:
        """Call an endpoint with no generated wrapper (currently only page move)."""
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Notion-Version": self._version,
            "Content-Type": "application/json",
        }
        async with self._semaphore:
            await self._pace()
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(
                    method, f"{_NOTION_API_BASE}{path}", headers=headers, json=json_body
                )
        if not 200 <= resp.status_code < 300:
            raise NotionSourceError(
                f"{context} failed: HTTP {resp.status_code}: {resp.text[:400]}"
            )
        return resp.json()

    async def _paginate(
        self, method: Any, *args: Any, context: str, **kwargs: Any
    ) -> list[dict[str, Any]]:
        """Follow ``next_cursor`` until exhausted, returning every ``results`` entry.

        Every paginated wrapper drops ``start_cursor=None`` from the query, so the first
        iteration needs no special case.
        """
        results: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            data = await self._call(
                method, *args, context=context, start_cursor=cursor, **kwargs
            )
            results.extend(data.get("results") or [])
            if not data.get("has_more") or not data.get("next_cursor"):
                return results
            cursor = data["next_cursor"]

    async def _paginate_body(
        self, method: Any, *args: Any, body: dict[str, Any], context: str
    ) -> list[dict[str, Any]]:
        """``_paginate`` for endpoints that take the cursor inside ``request_body``."""
        results: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            request_body = dict(body)
            if cursor:
                request_body["start_cursor"] = cursor
            data = await self._call(
                method, *args, request_body=request_body, context=context
            )
            results.extend(data.get("results") or [])
            if not data.get("has_more") or not data.get("next_cursor"):
                return results
            cursor = data["next_cursor"]

    # ------------------------------------------------------------------ bot / users

    async def retrieve_bot(self) -> dict[str, Any]:
        return await self._call(self.datasource.retrieve_bot_user, context="retrieve_bot_user")

    async def workspace_info(self) -> tuple[str, Optional[str]]:
        """Return ``(workspace_id, workspace_name)`` from the bot user — what the connector uses."""
        bot = await self.retrieve_bot()
        bot_data = bot.get("bot") or {}
        workspace_id = bot_data.get("workspace_id")
        if not workspace_id:
            raise NotionSourceError("Bot user has no workspace_id — check integration install")
        return str(workspace_id), bot_data.get("workspace_name")

    async def list_all_users(self) -> list[dict[str, Any]]:
        return await self._paginate(
            self.datasource.list_users, context="list_users", page_size=100
        )

    async def retrieve_user(self, user_id: str) -> dict[str, Any]:
        return await self._call(
            self.datasource.retrieve_user, user_id, context=f"retrieve_user({user_id})"
        )

    async def person_users_with_email(self) -> list[dict[str, Any]]:
        """Users the connector will sync: ``type == "person"`` with a visible email.

        Mirrors ``_transform_to_app_user`` — bots are skipped and a person without
        ``person.email`` is dropped with a warning.
        """
        synced: list[dict[str, Any]] = []
        for user in await self.list_all_users():
            if user.get("type") != "person" or not user.get("id"):
                continue
            detail = await self.retrieve_user(str(user["id"]))
            if (detail.get("person") or {}).get("email"):
                synced.append(detail)
        return synced

    # ------------------------------------------------------------------ search

    async def search_objects(self, object_type: str) -> list[dict[str, Any]]:
        """Every page or data_source shared with the integration (what the connector syncs)."""
        return await self._paginate_body(
            self.datasource.search,
            body={"filter": {"property": "object", "value": object_type}, "page_size": 100},
            context=f"search({object_type})",
        )

    # ------------------------------------------------------------------ pages

    async def create_page(
        self,
        parent: dict[str, Any],
        *,
        title: Optional[str] = None,
        properties: Optional[dict[str, Any]] = None,
        children: Optional[list[dict[str, Any]]] = None,
        icon: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"parent": parent}
        props = dict(properties or {})
        if title is not None:
            props.setdefault("title", {"title": rich_text(title)})
        body["properties"] = props
        if children:
            body["children"] = children
        if icon:
            body["icon"] = icon
        return await self._call(
            self.datasource.create_page, request_body=body,
            context=f"create_page({title or 'row'})",
        )

    async def create_child_page(
        self,
        parent_page_id: str,
        title: str,
        children: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        return await self.create_page(
            {"type": "page_id", "page_id": parent_page_id}, title=title, children=children
        )

    async def create_row_page(
        self,
        data_source_id: str,
        properties: dict[str, Any],
        children: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        return await self.create_page(
            {"type": "data_source_id", "data_source_id": data_source_id},
            properties=properties,
            children=children,
        )

    async def retrieve_page(self, page_id: str) -> dict[str, Any]:
        return await self._call(
            self.datasource.retrieve_page, page_id, context=f"retrieve_page({page_id})"
        )

    async def update_page(self, page_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self._call(
            self.datasource.update_page_properties, page_id, request_body=body,
            context=f"update_page({page_id})",
        )

    async def rename_page(
        self, page_id: str, title: str, *, title_property: str = "title"
    ) -> dict[str, Any]:
        return await self.update_page(
            page_id, {"properties": {title_property: {"title": rich_text(title)}}}
        )

    async def trash_page(self, page_id: str) -> dict[str, Any]:
        """Trash a page. Descendants inherit ``in_trash``, so this cascades over a subtree."""
        return await self.update_page(page_id, {"in_trash": True})

    async def move_page(self, page_id: str, new_parent: dict[str, Any]) -> dict[str, Any]:
        """``POST /v1/pages/{id}/move`` — the only way to change a page's parent."""
        return await self._raw(
            "POST", f"/pages/{page_id}/move", json_body={"parent": new_parent},
            context=f"move_page({page_id})",
        )

    # ------------------------------------------------------------------ databases

    async def create_database(
        self,
        parent_page_id: str,
        title: str,
        properties: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        body = {
            "parent": {"type": "page_id", "page_id": parent_page_id},
            "title": rich_text(title),
            "initial_data_source": {
                "properties": properties or {
                    "Name": {"title": {}},
                    "Tag": {"rich_text": {}},
                }
            },
        }
        return await self._call(
            self.datasource.create_database, request_body=body,
            context=f"create_database({title})",
        )

    async def retrieve_database(self, database_id: str) -> dict[str, Any]:
        return await self._call(
            self.datasource.retrieve_database, database_id,
            context=f"retrieve_database({database_id})",
        )

    async def first_data_source_id(self, database: dict[str, Any]) -> str:
        """Resolve a database's initial data source id from a create/retrieve payload."""
        sources = database.get("data_sources") or []
        if not sources:
            fetched = await self.retrieve_database(str(database["id"]))
            sources = fetched.get("data_sources") or []
        if not sources:
            raise NotionSourceError(f"database {database.get('id')} exposes no data_sources")
        return str(sources[0]["id"])

    async def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        return await self._call(
            self.datasource.retrieve_data_source_by_id, data_source_id,
            context=f"retrieve_data_source({data_source_id})",
        )

    async def update_data_source(
        self, database_id: str, data_source_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._call(
            self.datasource.update_data_source, database_id, data_source_id, request_body=body,
            context=f"update_data_source({data_source_id})",
        )

    async def rename_data_source(
        self, database_id: str, data_source_id: str, title: str
    ) -> dict[str, Any]:
        return await self.update_data_source(
            database_id, data_source_id, {"title": rich_text(title)}
        )

    async def add_data_source_column(
        self, database_id: str, data_source_id: str, column_name: str
    ) -> dict[str, Any]:
        return await self.update_data_source(
            database_id, data_source_id, {"properties": {column_name: {"rich_text": {}}}}
        )

    async def query_data_source_rows(self, data_source_id: str) -> list[dict[str, Any]]:
        return await self._paginate_body(
            self.datasource.query_data_source_by_id, data_source_id,
            body={"page_size": 100}, context=f"query_data_source({data_source_id})",
        )

    async def trash_database(self, database_id: str) -> dict[str, Any]:
        return await self._call(
            self.datasource.update_database, database_id, request_body={"in_trash": True},
            context=f"trash_database({database_id})",
        )

    # ------------------------------------------------------------------ blocks

    async def append_blocks(
        self, block_id: str, children: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Append children in chunks of 100 (Notion's per-request cap)."""
        appended: list[dict[str, Any]] = []
        for start in range(0, len(children), 100):
            data = await self._call(
                self.datasource.append_block_children, block_id,
                request_body={"children": children[start:start + 100]},
                context=f"append_block_children({block_id})",
            )
            appended.extend(data.get("results") or [])
        return appended

    async def list_block_children(self, block_id: str) -> list[dict[str, Any]]:
        return await self._paginate(
            self.datasource.retrieve_block_children, block_id,
            context=f"retrieve_block_children({block_id})", page_size=100,
        )

    async def retrieve_block(self, block_id: str) -> dict[str, Any]:
        return await self._call(
            self.datasource.retrieve_block, block_id, context=f"retrieve_block({block_id})"
        )

    async def delete_block(self, block_id: str) -> dict[str, Any]:
        return await self._call(
            self.datasource.delete_block, block_id, context=f"delete_block({block_id})"
        )

    # ------------------------------------------------------------------ comments

    async def create_comment(
        self,
        *,
        page_id: Optional[str] = None,
        block_id: Optional[str] = None,
        discussion_id: Optional[str] = None,
        text: str,
        file_upload_ids: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Create a page-level, block-level, or reply comment.

        Notion requires exactly one of ``parent.page_id`` / ``parent.block_id`` /
        ``discussion_id``.
        """
        body: dict[str, Any] = {"rich_text": rich_text(text)}
        if discussion_id:
            body["discussion_id"] = discussion_id
        elif block_id:
            body["parent"] = {"block_id": block_id}
        elif page_id:
            body["parent"] = {"page_id": page_id}
        else:
            raise ValueError("create_comment needs page_id, block_id, or discussion_id")

        if file_upload_ids:
            body["attachments"] = [
                {"type": "file_upload", "file_upload_id": fid} for fid in file_upload_ids
            ]
        return await self._call(
            self.datasource.create_comment, request_body=body, context="create_comment"
        )

    async def list_comments(self, block_id: str) -> list[dict[str, Any]]:
        return await self._paginate(
            self.datasource.retrieve_comments, context=f"retrieve_comments({block_id})",
            block_id=block_id, page_size=100,
        )

    # ------------------------------------------------------------------ file uploads

    async def upload_file(
        self, filename: str, content: bytes, content_type: str
    ) -> str:
        """Run the single-part upload flow and return the ``file_upload`` id."""
        created = await self._call(
            self.datasource.create_file_upload,
            request_body={
                "mode": "single_part",
                "filename": filename,
                "content_type": content_type,
            },
            context=f"create_file_upload({filename})",
        )
        upload_id = str(created["id"])
        sent = await self._call(
            self.datasource.send_file_upload, upload_id, content, filename, content_type,
            context=f"send_file_upload({filename})",
        )
        if sent.get("status") != "uploaded":
            raise NotionSourceError(
                f"upload {filename} ended in status {sent.get('status')!r}, expected 'uploaded'"
            )
        return upload_id

    async def upload_sample_file(self, relative_path: str, content_type: str) -> str:
        """Upload a binary from the shared ``pipeshub-ai/integration-test`` repo."""
        path = sample_file_path(relative_path)
        return await self.upload_file(path.name, path.read_bytes(), content_type)

    @staticmethod
    def sample_file_bytes(relative_path: str) -> bytes:
        """Bytes of a shared-repo sample file — asserts streamed content round-trips."""
        return sample_file_path(relative_path).read_bytes()
