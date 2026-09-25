"""Fakes for behaviour tests of the Slack connectors.

Only two things are faked. Slack itself is an in-memory workspace answering the
real ``slack_sdk.WebClient`` at the point where it opens a URL, so request
encoding, the SDK's error handling and our ``SlackDataSource`` wrapper all run
for real; file downloads go through ``httpx.MockTransport``. Our databases
(records, record groups, users, roles and sync checkpoints) are kept in memory
with the same "a save replaces what was stored" rule the real processor uses,
so a second sync sees what the first one wrote.
"""

from __future__ import annotations

import io
import json
import time
import urllib.error
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from http.client import HTTPMessage
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from app.models.entities import MessageRecord, Record, RecordGroup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from urllib.request import Request

TEAM_ID = "T0ACME"
DOMAIN = "acme"
BOT_TOKEN = "xoxb-bot-token-1"
USER_TOKEN = "xoxp-user-token-1"


def ts_minutes_ago(minutes: float) -> str:
    """A Slack message timestamp ``minutes`` before now, in Slack's 6-decimal form."""
    return f"{time.time() - minutes * 60:.6f}"


@dataclass
class SlackCall:
    method: str
    params: dict[str, str]
    token: Optional[str]


@dataclass
class SlackError:
    """A Slack failure reported the usual way: HTTP 200 with ``ok: false``."""
    code: str


@dataclass
class RateLimited:
    """Slack's rate limiting: a real HTTP 429 carrying ``Retry-After``."""
    retry_after: int = 30


@dataclass
class HttpFailure:
    status: int = 500


@dataclass
class _Fault:
    method: str
    reply: object
    times: Optional[int]
    when: Optional[Callable[[dict[str, str]], bool]]


@dataclass
class SlackWorkspace:
    """A Slack workspace answering the Web API methods the connectors call.

    Channels, members, users and messages live in plain dicts that tests edit
    between syncs. ``page_size`` caps how many items one page returns for a
    method, which is how pagination is staged. ``fail`` injects failures for
    matching calls; every call is recorded in ``calls``.
    """

    users: list[dict[str, Any]] = field(default_factory=list)
    channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    members: dict[str, list[str]] = field(default_factory=dict)
    history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    replies: dict[tuple[str, str], list[dict[str, Any]]] = field(default_factory=dict)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    downloads: dict[str, object] = field(default_factory=dict)
    usergroups: list[dict[str, Any]] = field(default_factory=list)
    page_size: dict[str, int] = field(default_factory=dict)
    valid_tokens: set[str] = field(default_factory=lambda: {BOT_TOKEN, USER_TOKEN})
    auth_user_id: Optional[str] = None
    calls: list[SlackCall] = field(default_factory=list)
    downloads_seen: list[httpx.Request] = field(default_factory=list)
    hooks: list[tuple[str, Callable[[dict[str, str]], None]]] = field(default_factory=list)
    _faults: list[_Fault] = field(default_factory=list)

    # ── building the workspace ────────────────────────────────────────────

    def add_user(
        self, uid: str, email: Optional[str], name: str, *,
        guest: bool = False, bot: bool = False, deleted: bool = False,
    ) -> dict[str, Any]:
        user = {
            "id": uid, "name": name.lower(), "deleted": deleted, "is_bot": bot,
            "is_restricted": guest, "is_ultra_restricted": False,
            "profile": {"real_name": name, "display_name": name, **({"email": email} if email else {})},
        }
        self.users.append(user)
        return user

    def add_channel(
        self, cid: str, name: str, *, kind: str = "public", members: Optional[list[str]] = None,
        archived: bool = False, is_member: bool = True, dm_with: Optional[str] = None,
    ) -> dict[str, Any]:
        channel: dict[str, Any] = {
            "id": cid, "name": name, "created": int(time.time()) - 86400 * 60,
            "is_channel": kind in ("public", "private"), "is_private": kind in ("private", "mpim"),
            "is_im": kind == "im", "is_mpim": kind == "mpim", "is_archived": archived,
            "is_member": is_member, "topic": {"value": f"{name} topic"},
        }
        if kind == "im":
            channel["user"] = dm_with
            channel.pop("name")
        self.channels[cid] = channel
        self.members[cid] = list(members or [])
        self.history.setdefault(cid, [])
        return channel

    def post(self, cid: str, ts: str, user: str, text: str, **extra: object) -> dict[str, Any]:
        msg = {"type": "message", "ts": ts, "user": user, "text": text, **extra}
        self.history[cid].append(msg)
        return msg

    def reply(self, cid: str, thread_ts: str, ts: str, user: str, text: str, **extra: object) -> dict[str, Any]:
        parent = next(m for m in self.history[cid] if m["ts"] == thread_ts)
        msg = {"type": "message", "ts": ts, "user": user, "text": text, "thread_ts": thread_ts, **extra}
        thread = self.replies.setdefault((cid, thread_ts), [])
        thread.append(msg)
        parent["thread_ts"] = thread_ts
        parent["reply_count"] = len(thread)
        parent["latest_reply"] = max(m["ts"] for m in thread)
        return msg

    def add_file(
        self, fid: str, name: str, content: bytes = b"file-bytes", *,
        mimetype: str = "application/pdf", filetype: str = "pdf",
    ) -> dict[str, Any]:
        url = f"https://files.slack.com/files-pri/{TEAM_ID}-{fid}/download/{name}"
        fd = {
            "id": fid, "name": name, "mimetype": mimetype, "filetype": filetype,
            "size": len(content), "created": int(time.time()) - 3600,
            "url_private": url, "url_private_download": url,
            "permalink": f"https://{DOMAIN}.slack.com/files/U1/{fid}/{name}",
        }
        self.files[fid] = fd
        self.downloads[url] = content
        return fd

    # ── staging failures ──────────────────────────────────────────────────

    def fail(
        self, method: str, reply: object, *, times: Optional[int] = 1,
        when: Optional[Callable[[dict[str, str]], bool]] = None,
    ) -> None:
        """Answer the next ``times`` matching calls to ``method`` with ``reply`` (None = forever)."""
        self._faults.append(_Fault(method, reply, times, when))

    def on_call(self, method: str, hook: Callable[[dict[str, str]], None]) -> None:
        """Run ``hook(params)`` before answering each call to ``method``."""
        self.hooks.append((method, hook))

    def calls_to(self, method: str) -> list[SlackCall]:
        return [c for c in self.calls if c.method == method]

    # ── the transport ─────────────────────────────────────────────────────

    def urlopen(self, req: Request, *_: object, **__: object) -> "_FakeHTTPResponse":
        """Stands in for ``urllib.request.urlopen`` inside ``slack_sdk``."""
        parsed = urlparse(req.full_url)
        if parsed.hostname not in ("slack.com", "www.slack.com") or not parsed.path.startswith("/api/"):
            raise AssertionError(f"unexpected Slack URL {req.full_url}")
        method = parsed.path[len("/api/"):]
        params = {k: v[0] for k, v in parse_qs(parsed.query, keep_blank_values=True).items()}
        if req.data:
            params.update({k: v[0] for k, v in parse_qs(req.data.decode(), keep_blank_values=True).items()})
        auth = req.get_header("Authorization") or ""
        token = auth.removeprefix("Bearer ") or None
        self.calls.append(SlackCall(method, params, token))

        for hook_method, hook in list(self.hooks):
            if hook_method == method:
                hook(params)

        for fault in list(self._faults):
            if fault.method == method and (fault.when is None or fault.when(params)):
                if fault.times is not None:
                    fault.times -= 1
                    if fault.times <= 0:
                        self._faults.remove(fault)
                return self._render_failure(req.full_url, fault.reply)

        if token not in self.valid_tokens:
            return _json(200, {"ok": False, "error": "invalid_auth"})
        handler = getattr(self, "_api_" + method.replace(".", "_"), None)
        if handler is None:
            return _json(200, {"ok": False, "error": "unknown_method"})
        return _json(200, handler(params))

    @staticmethod
    def _render_failure(url: str, reply: object) -> "_FakeHTTPResponse":
        if isinstance(reply, SlackError):
            return _json(200, {"ok": False, "error": reply.code})
        if isinstance(reply, RateLimited):
            raise _http_error(url, 429, {"ok": False, "error": "ratelimited"}, {"Retry-After": str(reply.retry_after)})
        if isinstance(reply, HttpFailure):
            raise _http_error(url, reply.status, {"ok": False, "error": "internal_error"}, {})
        raise AssertionError(f"unknown fault {reply!r}")

    def file_host(self, request: httpx.Request) -> httpx.Response:
        """Serves ``files.slack.com`` downloads to the connector's httpx client."""
        self.downloads_seen.append(request)
        body = self.downloads.get(str(request.url))
        if body is None:
            return httpx.Response(404, text="not found")
        if isinstance(body, httpx.Response):
            return body
        if request.headers.get("authorization", "").removeprefix("Bearer ") not in self.valid_tokens:
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>sign in</html>")
        return httpx.Response(200, headers={"content-type": "application/octet-stream"}, content=body)

    # ── pagination ────────────────────────────────────────────────────────

    def _page(self, method: str, items: list[Any], params: dict[str, str]) -> tuple[list[Any], str]:
        limit = min(int(params.get("limit") or 100), self.page_size.get(method, 10**6))
        cursor = params.get("cursor") or ""
        offset = int(cursor.removeprefix("page:")) if cursor else 0
        page = items[offset: offset + limit]
        more = offset + limit < len(items)
        return page, (f"page:{offset + limit}" if more else "")

    @staticmethod
    def _in_window(ts: str, params: dict[str, str]) -> bool:
        inclusive = params.get("inclusive") in ("1", "true")
        value = float(ts)
        if params.get("oldest"):
            oldest = float(params["oldest"])
            if value < oldest or (value == oldest and not inclusive):
                return False
        if params.get("latest"):
            latest = float(params["latest"])
            if value > latest or (value == latest and not inclusive):
                return False
        return True

    # ── Web API methods ───────────────────────────────────────────────────

    def _api_auth_test(self, params: dict[str, str]) -> dict[str, Any]:
        return {"ok": True, "url": f"https://{DOMAIN}.slack.com/", "team": "Acme", "team_id": TEAM_ID,
                "user_id": self.auth_user_id or "UBOT"}

    def _api_team_info(self, params: dict[str, str]) -> dict[str, Any]:
        return {"ok": True, "team": {"id": TEAM_ID, "name": "Acme", "domain": DOMAIN}}

    def _api_users_list(self, params: dict[str, str]) -> dict[str, Any]:
        page, nxt = self._page("users.list", self.users, params)
        return {"ok": True, "members": page, "response_metadata": {"next_cursor": nxt}}

    def _api_users_info(self, params: dict[str, str]) -> dict[str, Any]:
        user = next((u for u in self.users if u["id"] == params.get("user")), None)
        return {"ok": True, "user": user} if user else {"ok": False, "error": "user_not_found"}

    def _api_usergroups_list(self, params: dict[str, str]) -> dict[str, Any]:
        return {"ok": True, "usergroups": self.usergroups}

    def _api_usergroups_users_list(self, params: dict[str, str]) -> dict[str, Any]:
        group = next((g for g in self.usergroups if g["id"] == params.get("usergroup")), None)
        return {"ok": True, "users": (group or {}).get("users", [])}

    @staticmethod
    def _channel_type(channel: dict[str, Any]) -> str:
        if channel["is_im"]:
            return "im"
        if channel["is_mpim"]:
            return "mpim"
        return "private_channel" if channel["is_private"] else "public_channel"

    def _api_conversations_list(self, params: dict[str, str]) -> dict[str, Any]:
        types = set((params.get("types") or "public_channel").split(","))
        skip_archived = params.get("exclude_archived") in ("1", "true")
        listed = [
            c for c in self.channels.values()
            if self._channel_type(c) in types and not (skip_archived and c["is_archived"])
        ]
        page, nxt = self._page("conversations.list", listed, params)
        return {"ok": True, "channels": page, "response_metadata": {"next_cursor": nxt}}

    def _api_conversations_members(self, params: dict[str, str]) -> dict[str, Any]:
        cid = params.get("channel", "")
        if cid not in self.channels:
            return {"ok": False, "error": "channel_not_found"}
        page, nxt = self._page("conversations.members", self.members[cid], params)
        return {"ok": True, "members": page, "response_metadata": {"next_cursor": nxt}}

    def _api_conversations_join(self, params: dict[str, str]) -> dict[str, Any]:
        channel = self.channels.get(params.get("channel", ""))
        if not channel:
            return {"ok": False, "error": "channel_not_found"}
        if channel["is_private"]:
            return {"ok": False, "error": "method_not_supported_for_channel_type"}
        channel["is_member"] = True
        return {"ok": True, "channel": channel}

    def _api_conversations_history(self, params: dict[str, str]) -> dict[str, Any]:
        cid = params.get("channel", "")
        channel = self.channels.get(cid)
        if not channel:
            return {"ok": False, "error": "channel_not_found"}
        if not channel["is_member"]:
            return {"ok": False, "error": "not_in_channel"}
        msgs = sorted(
            (m for m in self.history[cid] if self._in_window(m["ts"], params)),
            key=lambda m: float(m["ts"]), reverse=True,
        )
        page, nxt = self._page("conversations.history", msgs, params)
        return {"ok": True, "messages": page, "has_more": bool(nxt), "response_metadata": {"next_cursor": nxt}}

    def _api_conversations_replies(self, params: dict[str, str]) -> dict[str, Any]:
        cid, thread_ts = params.get("channel", ""), params.get("ts", "")
        parent = next((m for m in self.history.get(cid, []) if m["ts"] == thread_ts), None)
        if parent is None:
            return {"ok": False, "error": "thread_not_found"}
        thread = sorted(
            (m for m in self.replies.get((cid, thread_ts), []) if self._in_window(m["ts"], params)),
            key=lambda m: float(m["ts"]),
        )
        page, nxt = self._page("conversations.replies", [parent, *thread], params)
        return {"ok": True, "messages": page, "has_more": bool(nxt), "response_metadata": {"next_cursor": nxt}}

    def _api_files_info(self, params: dict[str, str]) -> dict[str, Any]:
        fd = self.files.get(params.get("file", ""))
        return {"ok": True, "file": fd} if fd else {"ok": False, "error": "file_not_found"}


class _FakeHTTPResponse:
    def __init__(self, status: int, body: bytes, headers: HTTPMessage) -> None:
        self.code = self.status = status
        self.headers = headers
        self._body = body

    def read(self) -> bytes:
        return self._body


def _headers(extra: dict[str, str]) -> HTTPMessage:
    msg = HTTPMessage()
    msg["Content-Type"] = "application/json; charset=utf-8"
    for key, value in extra.items():
        msg[key] = value
    return msg


def _json(status: int, payload: dict[str, Any]) -> _FakeHTTPResponse:
    return _FakeHTTPResponse(status, json.dumps(payload).encode(), _headers({}))


def _http_error(url: str, status: int, payload: dict[str, Any], headers: dict[str, str]) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, status, "error", _headers(headers), io.BytesIO(json.dumps(payload).encode()))


class FakeSlackStore:
    """In-memory stand-in for ``DataSourceEntitiesProcessor``.

    Saving a record group or a role replaces its stored access list, exactly
    like the real processor (it deletes the old permission edges first), so a
    test can see access being wiped. Only the methods the Slack connectors call
    are implemented.
    """

    def __init__(self, org_id: str = "org-1", creator_email: Optional[str] = None) -> None:
        self.org_id = org_id
        self.creator_email = creator_email
        self.records: dict[str, Record] = {}
        self.record_groups: dict[str, Any] = {}
        self.group_access: dict[str, list[Any]] = {}
        self.app_users: dict[str, Any] = {}
        self.roles: dict[str, tuple[Any, list[Any]]] = {}
        self.user_groups: dict[str, tuple[Any, list[Any]]] = {}
        self.record_batches: list[list[Record]] = []
        self.content_updates: list[Record] = []
        self.reindexed: list[Record] = []

    async def on_new_app_users(self, users: list[Any]) -> None:
        for user in users:
            existing = self.app_users.get(user.source_user_id)
            if existing:
                user.id = existing.id
            self.app_users[user.source_user_id] = user

    async def get_all_app_users(self, connector_id: str) -> list[Any]:
        return list(self.app_users.values())

    async def get_app_creator_user(self, connector_id: str) -> Optional[SimpleNamespace]:
        return SimpleNamespace(email=self.creator_email) if self.creator_email else None

    async def on_new_app_roles(self, roles: list[tuple[Any, list[Any]]]) -> None:
        for role, members in roles:
            self.roles[role.source_role_id] = (role, list(members))

    async def on_new_user_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, members in groups:
            self.user_groups[group.source_user_group_id] = (group, list(members))

    async def on_new_record_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        for group, permissions in groups:
            existing = self.record_groups.get(group.external_group_id)
            if existing:
                group.id = existing.id
            self.record_groups[group.external_group_id] = group
            self.group_access[group.external_group_id] = list(permissions)

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> Optional[RecordGroup]:
        return self.record_groups.get(external_id)

    async def on_new_records(self, records_with_permissions: list[tuple[Record, list[Any]]]) -> None:
        self.record_batches.append([rec for rec, _ in records_with_permissions])
        for record, _ in records_with_permissions:
            existing = self.records.get(record.external_record_id)
            if existing:
                record.id = existing.id
            self.records[record.external_record_id] = record

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Optional[Record]:
        return self.records.get(external_record_id)

    async def get_record_by_weburl(self, weburl: str) -> Optional[Record]:
        return None

    async def get_records_by_parent(
        self, connector_id: str, parent_external_record_id: str, record_type: Optional[str] = None,
    ) -> list[Record]:
        return [
            r for r in self.records.values()
            if r.parent_external_record_id == parent_external_record_id
            and (record_type is None or r.record_type.value == record_type)
        ]

    async def on_record_content_update(self, record: Record) -> None:
        self.content_updates.append(record)
        self.records[record.external_record_id] = record

    async def on_updated_record_permissions(self, record: Record, permissions: list[Any]) -> None:
        raise AssertionError("Slack records inherit access from their channel")

    async def reindex_existing_records(self, records: list[Record]) -> None:
        self.reindexed.extend(records)

    # ── assertions helpers ────────────────────────────────────────────────

    def access_emails(self, channel_id: str) -> set[str]:
        return {p.email for p in self.group_access.get(channel_id, []) if p.email}

    def messages(self) -> list[MessageRecord]:
        return [r for r in self.records.values() if isinstance(r, MessageRecord)]

    def message_ts_count(self) -> dict[str, int]:
        """How many stored message records carry each Slack message (by its ts)."""
        counts: dict[str, int] = {}
        for record in self.messages():
            for block in record.block_containers.blocks:
                counts[block.source_id] = counts.get(block.source_id, 0) + 1
        return counts

    def written_ts(self) -> list[str]:
        """Every Slack message ts written by ``on_new_records``, one entry per write."""
        seen: list[str] = []
        for batch in self.record_batches:
            for record in batch:
                if isinstance(record, MessageRecord):
                    seen.extend(b.source_id for b in record.block_containers.blocks)
        return seen


class FakeCheckpoints:
    """In-memory sync-point collection behind ``DataStoreProvider.transaction()``."""

    def __init__(self) -> None:
        self.sync_points: dict[str, dict[str, Any]] = {}

    async def get_sync_point(self, key: str, raise_on_error: bool = False) -> Optional[dict[str, Any]]:
        return self.sync_points.get(key)

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.sync_points[key] = dict(data)

    async def delete_sync_point(self, key: str) -> None:
        self.sync_points.pop(key, None)

    def value(self, key_suffix: str) -> Optional[dict[str, Any]]:
        for key, data in self.sync_points.items():
            if key.endswith(key_suffix):
                return data
        return None

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator["FakeCheckpoints"]:
        yield self


class FakeConfigService:
    """Serves one connector's etcd config document."""

    def __init__(self, connector_id: str, config: dict[str, Any]) -> None:
        self.connector_id = connector_id
        self.config = config

    async def get_config(self, path: str, default: object = None, **_: object) -> object:
        if path == f"/services/connectors/{self.connector_id}/config":
            return self.config
        return default
