"""Local repositories: a client pushes a working tree, the server mirrors it and indexes it like any code repository.

The caller's org and user come from the token. A repository is named only by an opaque key the client chooses; the server derives
the storage directory from (org, user, key), so no request ever names a server path and no user can address another user's
repository. The mirror is read by the Local Repository connector (a Folder connector whose root is that directory), so records,
code indexing, symbols, edges and permissions are the connector framework's. See ``app/connectors/sources/local_repo``.
"""
from __future__ import annotations  # noqa: I001

import asyncio
import hashlib
import json
import logging
import unicodedata
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.middlewares.auth import require_scopes
from app.config.constants.service import OAuthScopes
from app.connectors.api.router import (
    create_connector_instance,
    delete_connector_instance,
    get_graph_provider,
    toggle_connector_instance,
    update_connector_instance_name,
)
from app.connectors.core.constants import ConnectorRequestKeys as K
from app.config.constants.arangodb import CollectionNames, ProgressStatus  # noqa: F401
from app.modules.code_graph.enabled import CODE_GRAPH_ENABLED
from app.connectors.core.sync.sync_dispatcher import SyncSpec, get_dispatcher
from app.connectors.sources.local_folder import scan
from app.connectors.sources.local_repo import store
from app.connectors.sources.local_repo.connector import (
    LOCAL_REPO_CONNECTOR_NAME,
    REPO_KEY,
)
from app.core.org_context import MissingOrgContext, SystemContextNotAllowed, current_org_id
from app.utils.time_conversion import get_epoch_timestamp_in_ms

logger = logging.getLogger(__name__)

# The same ceiling as the Node API's JSON body limit. Enforced on the bytes that actually arrive, before FastAPI buffers or parses the body
# (a dependency runs after that), so a client that talks to this service directly cannot make it hold more than this per request.
MAX_BODY_BYTES = 10 * 1024 * 1024


class BodyLimit:
    """ASGI middleware: a request to /api/v1/local-repos whose body is over ``MAX_BODY_BYTES`` (declared, or counted as it streams) is a 413.

    The body is read here, at most ``MAX_BODY_BYTES`` of it, and handed on whole: the limit holds before the framework buffers or parses anything,
    with or without a Content-Length, and the most one request can make this service hold is the limit itself."""

    def __init__(self, app: Any) -> None:  # noqa: ANN401
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:  # noqa: ANN401
        if scope["type"] != "http" or not scope["path"].startswith("/api/v1/local-repos") or scope["method"] not in ("POST", "PUT"):
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > MAX_BODY_BYTES):
            await self._refuse(send)
            return
        chunks: list[bytes] = []
        seen = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            seen += len(message.get("body", b""))
            if seen > MAX_BODY_BYTES:
                await self._refuse(send)
                return
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> dict[str, Any]:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()  # after the body: what the server says next (a disconnect)

        await self.app(scope, replay, send)

    @staticmethod
    async def _refuse(send: Any) -> None:  # noqa: ANN401
        body = json.dumps({"detail": f"A request body is at most {MAX_BODY_BYTES} bytes"}).encode()
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


router = APIRouter(prefix="/api/v1/local-repos", tags=["Local repositories"])
WRITE = Depends(require_scopes(OAuthScopes.CODE_WRITE))
READ = Depends(require_scopes(OAuthScopes.CODE_READ, OAuthScopes.CODE_WRITE))

# A coalesced sync is started again by a status poll, but not more often than this.
REKICK_AFTER_MS = 20_000
# A commit that is still not synced this long after it was made, with no sync running, is reported as stuck instead of syncing for ever.
STUCK_AFTER_MS = 120_000
# shortcut: these locks, and the store's thread locks, serialise work inside one process. They hold because the connectors service runs one
# worker (app.edition_services.max_connector_workers() == 1); with more, two processes could write one mirror at once, so move the locks
# to the database (or a file lock on the repository directory) before raising that number.
_register_locks: dict[str, asyncio.Lock] = {}
_user_register_locks: dict[tuple[str, str], asyncio.Lock] = {}  # taken before the repository's own lock: the repository count is checked, then spent


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a field this contract does not name (a path, say) is refused, not ignored


class RegisterBody(_Body):
    name: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^[^<>]*$")

    @field_validator("name")
    @classmethod
    def _no_format_characters(cls, value: str | None) -> str | None:
        # Bidi overrides and other format characters (category Cf) make a name read as another one in a list.
        if value is not None and any(unicodedata.category(c) == "Cf" or ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("a name has no control or format characters")
        return value


class ManifestFile(_Body):
    path: str
    sha256: str
    size: int


class ManifestBody(_Body):
    files: list[ManifestFile] = Field(default_factory=list)
    complete: bool = False
    deleted: list[str] = Field(default_factory=list, max_length=100_000)  # the exact cap is the repository's maxManifestEntries, checked in the route


class CommitBody(_Body):
    # What the client left out before sending, by reason (a count per reason, nothing about the files).
    excluded: dict[str, int] = Field(default_factory=dict, max_length=20)


class UploadFile(_Body):
    path: str
    sha256: str
    content: str


class UploadBody(_Body):
    files: list[UploadFile]


def _caller(request: Request) -> tuple[str, str]:
    """The org and user of the authenticated request. The org is the one the auth layer bound for this request (from the verified token,
    never from anything the client sent), read from the ambient context like every other route."""
    try:
        org_id = current_org_id()
    except (MissingOrgContext, SystemContextNotAllowed) as exc:
        raise HTTPException(status_code=401, detail="User not authenticated") from exc
    user_id = (getattr(request.state, "user", None) or {}).get("userId")
    if not user_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    return org_id, user_id


def _repo(request: Request, key: str) -> store.Repo:
    org_id, user_id = _caller(request)
    try:
        return store.Repo(org_id, user_id, key)
    except store.StoreError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid repository key ({exc.reason}): use 8-64 letters, digits, - or _") from exc


def _existing(request: Request, key: str) -> store.Repo:
    repo = _repo(request, key)
    if not repo.exists():
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


def _as_json_request(request: Request, payload: dict[str, Any]) -> Request:
    """The same authenticated request, carrying ``payload`` as its JSON body, for the connector routes this module drives."""
    body, sent = json.dumps(payload).encode(), False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(request.scope, receive)


async def _instance(request: Request, connector_id: str | None) -> dict[str, Any] | None:
    """The caller's connector instance, or None when it is confirmed not to exist (never made, or already deleted).

    The registry answers None for a read error as well as for a missing connector, and acting on that would re-create or skip deleting a
    connector that is there. So a None is checked against the graph: a lookup that fails, or a connector that exists but was not returned, is a
    503, never "absent"."""
    if not connector_id:
        return None
    org_id, user_id = _caller(request)
    unavailable = HTTPException(status_code=503, detail="The connector of this repository could not be read; try again in a moment")
    try:
        found = await request.app.state.connector_registry.get_connector_instance(
            connector_id=connector_id, user_id=user_id, org_id=org_id, is_admin=False)
        if found is not None:
            return found
        stored = await request.app.state.graph_provider.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True)
    except Exception as exc:
        logger.warning("local repo: could not read connector %s", connector_id, exc_info=True)
        raise unavailable from exc
    if stored is None:
        return None
    logger.warning("local repo: connector %s exists but the registry did not return it", connector_id)
    raise unavailable


async def _kick(request: Request, repo: store.Repo, connector_id: str) -> None:
    org_id, _ = _caller(request)
    dispatcher = get_dispatcher()
    if dispatcher is None:
        return  # the connector's own schedule will still pick the files up
    await dispatcher.submit(SyncSpec(connector_id=connector_id, connector_name=LOCAL_REPO_CONNECTOR_NAME, org_id=org_id))
    await asyncio.to_thread(repo.save_meta, kickedAt=get_epoch_timestamp_in_ms())


async def _status(request: Request, repo: store.Repo, *, with_limits: bool = False) -> dict[str, Any]:
    org_id, user_id = _caller(request)
    meta = await asyncio.to_thread(repo.meta)
    manifest = await asyncio.to_thread(repo.manifest)
    generation, committed_at = int(meta.get("generation", 0)), int(meta.get("committedAt", 0))
    out: dict[str, Any] = {
        "repoKey": repo.key, "name": meta.get("name"), "connectorId": meta.get("connectorId"), "generation": generation,
        "files": len(manifest), "lastCommitAt": committed_at or None,
    }
    if with_limits:
        out["limits"] = repo.limits
    instance = await _instance(request, meta.get("connectorId"))
    if instance is None:
        out.update(state="empty" if generation == 0 else "syncing", syncedGeneration=0)
        return out
    started = int(instance.get("lastSyncStartedAtTimestamp") or 0)
    outcome = instance.get("lastSyncOutcome")
    finished = int(instance.get("lastSyncFinishedAtTimestamp") or 0)
    # lastSyncOutcome is the last run that *ended*: while this commit's run is going, it is still the previous run's.
    # So the run counts only once it has both started after the commit and finished after it started.
    covers_commit = committed_at > 0 and started >= committed_at and finished >= started
    out["syncedGeneration"] = generation if covers_commit and outcome == "succeeded" else int(meta.get("syncedGeneration", 0))
    if generation == 0:
        out["state"] = "empty"
    elif covers_commit and outcome == "failed":
        out["state"] = "failed"
        out["error"] = "the last sync of this repository failed"
    elif not (covers_commit and outcome == "succeeded"):
        out["state"] = "syncing"
        dispatcher = get_dispatcher()
        running = dispatcher is not None and await dispatcher.is_running(meta["connectorId"])
        now = get_epoch_timestamp_in_ms()
        if now - int(meta.get("kickedAt", 0)) > REKICK_AFTER_MS and not running:
            await _kick(request, repo, meta["connectorId"])
        if not running and now - committed_at > STUCK_AFTER_MS:
            out["state"] = "stuck"
            out["error"] = "the sync of this repository did not start; the files are stored. Check that the connectors service is running, then commit again."
    else:
        # Files the sync left out on purpose (a generator's header, say) never get a record: they are not "pushed and not yet visible".
        left_out = meta.get("excludedFiles") or {}
        awaiting = sum(1 for path in manifest if scan.safe_name(path) not in left_out)
        out.update(await _indexing(request, org_id, user_id, meta["connectorId"], int(meta.get("codeChangedAt", 0)), awaiting))
        excluded: dict[str, int] = dict(meta.get("clientExcluded") or {})
        for why in (meta.get("excludedFiles") or {}).values():
            excluded[why] = excluded.get(why, 0) + 1
        if excluded:
            out["indexing"]["excluded"] = excluded  # files that were left out altogether (stored nowhere), by reason
    return out


async def _indexing(request: Request, org_id: str, user_id: str, connector_id: str, code_changed_at: int = 0, files_pushed: int = 0) -> dict[str, Any]:
    graph = request.app.state.graph_provider
    stats = (await graph.get_connector_stats(org_id, connector_id)).get("data") or {}
    by = (stats.get("stats") or {}).get("indexingStatus") or {}
    total = int((stats.get("stats") or {}).get("total") or 0)
    counts = {"total": total, "notStarted": by.get("NOT_STARTED", 0), "queued": by.get("QUEUED", 0),
              "inProgress": by.get("IN_PROGRESS", 0), "completed": by.get("COMPLETED", 0), "failed": by.get("FAILED", 0)}
    counts["skipped"] = max(0, total - sum(counts[k] for k in ("notStarted", "queued", "inProgress", "completed", "failed")))
    # One file that cannot be processed (a corrupt image, say) must not fail the repository (it is failed only when nothing was indexed), but it makes it "partial", not "ready".
    state = ("failed" if counts["failed"] and not counts["completed"] and not (counts["notStarted"] + counts["queued"] + counts["inProgress"])
             else "indexing" if counts["notStarted"] + counts["queued"] + counts["inProgress"] else "ready")
    out: dict[str, Any] = {"indexing": counts}
    if state == "ready" and counts["completed"] and CODE_GRAPH_ENABLED:  # with the kill switch off no graph is built, so none is waited for
        graph_state, built_at = await _graph_status(graph, org_id, user_id, connector_id)
        out["codeGraph"] = graph_state
        if graph_state in ("never_built", "building", "waiting_on_indexing", "retrying", "stale", "unknown"):
            state = "indexing"
        elif graph_state in ("failed", "too_large"):
            state = "failed"
        elif graph_state == "ready" and code_changed_at > (built_at or 0):
            # built_at is when the last build STARTED (the code graph's lastEdgeBuildAt): a build that began before the last upload cannot have
            # seen it, however late it finished.
            # Every record is done, but the last build predates the files pushed since: the build has not been asked for yet.
            out["codeGraph"], state = "stale", "indexing"
    if state == "ready" and files_pushed and not total:
        state = "indexing"  # records of the synced files are not visible yet: never "ready" with nothing to read
    if state == "ready" and counts["failed"]:
        # Searchable, but never "ready" while a record is FAILED: it is missing from the index.
        state = "partial"
        out["note"] = f"{counts['failed']} file(s) failed to index and cannot be searched; the rest is ready."
    out["state"] = state
    return out


async def _graph_status(graph: Any, org_id: str, user_id: str, connector_id: str) -> tuple[str, int | None]:  # noqa: ANN401
    """The code graph's status for this connector's repository, and when it was last built."""
    from app.agents.actions.code_graph.repos import list_code_repos
    from app.modules.code_graph.build_state import BuildStateStore, graph_status

    try:
        repos = [r for r in await list_code_repos(graph, org_id, user_id) if r.connector_id == connector_id]  # the list holds only repos with a readable code file
        if not repos:
            return "no_code", None  # no readable code file: nothing for a graph to be built from
        state = await BuildStateStore(graph).read(org_id, repos[0].group_id)
        return graph_status(state).value, (state.last_build if state else None)
    except Exception:
        logger.warning("local repo: code graph status unreadable for %s", connector_id, exc_info=True)
        return "unknown", None


# --------------------------------------------------------------------------------------------- calls
@router.put("/{repo_key}", dependencies=[WRITE])
async def register(request: Request, repo_key: str, body: RegisterBody | None = None) -> JSONResponse:
    repo = _repo(request, repo_key)
    org_id, user_id = _caller(request)
    name = (body.name if body and body.name else None) or "local-repository"
    # Connector names are unique per user; two clones with one folder name must both register.
    instance_name = f"{name} [{hashlib.sha256(repo_key.encode()).hexdigest()[:10]}]"  # of the whole key: keys may share a prefix
    async with _user_register_locks.setdefault((org_id, user_id), asyncio.Lock()), _register_locks.setdefault(str(repo.dir), asyncio.Lock()):
        meta = await asyncio.to_thread(repo.meta)
        graph = await get_graph_provider(request)
        instance = await _instance(request, meta.get("connectorId"))
        if instance is not None and instance.get("status") == "DELETING":
            raise HTTPException(status_code=409, detail="This repository is being deleted")
        created = instance is None
        if created and not repo.exists() and await asyncio.to_thread(repo.user_repos) >= repo.limits["maxReposPerUser"]:
            raise HTTPException(status_code=409, detail=f"You already have {repo.limits['maxReposPerUser']} repositories on this server, the most one person may keep. Remove one first.")
        if created:
            await asyncio.to_thread(repo.save_meta, name=name, connectorId=None)
            made = await create_connector_instance(_as_json_request(request, {
                K.CONNECTOR_TYPE: LOCAL_REPO_CONNECTOR_NAME, K.INSTANCE_NAME: instance_name, K.SCOPE: "personal",
                K.CONFIG: {"auth": {}, "sync": {"selectedStrategy": "SCHEDULED", "customValues": {REPO_KEY: repo_key}}},
            }), graph)
            connector_id = made["connector"]["connectorId"]
            await asyncio.to_thread(repo.save_meta, connectorId=connector_id)
            await toggle_connector_instance(connector_id, _as_json_request(request, {"type": "sync"}), graph)
        elif not instance.get("isActive"):  # an earlier register created it but could not turn it on
            await toggle_connector_instance(meta["connectorId"], _as_json_request(request, {"type": "sync"}), graph)
        if not created and meta.get("name") != name and body and body.name:
            await update_connector_instance_name(meta["connectorId"], _as_json_request(request, {"instanceName": instance_name}), graph)
            await asyncio.to_thread(repo.save_meta, name=name)
    return JSONResponse(status_code=201 if created else 200, content={**await _status(request, repo, with_limits=True), "created": created})


@router.post("/{repo_key}/manifest", dependencies=[WRITE])
async def manifest(request: Request, repo_key: str, body: ManifestBody) -> dict[str, Any]:
    repo = _existing(request, repo_key)
    if len(body.files) > repo.limits["maxManifestEntries"]:
        raise HTTPException(status_code=413, detail=f"At most {repo.limits['maxManifestEntries']} manifest entries per call")
    if len(body.deleted) > repo.limits["maxManifestEntries"]:
        raise HTTPException(status_code=413, detail=f"At most {repo.limits['maxManifestEntries']} deleted paths per call")
    diff = await asyncio.to_thread(repo.diff, [f.model_dump() for f in body.files], body.complete, body.deleted)
    return {"upload": diff.upload, "deleted": diff.deleted, "unchanged": diff.unchanged, "rejected": diff.rejected}


@router.put("/{repo_key}/files", dependencies=[WRITE])
async def upload(request: Request, repo_key: str, body: UploadBody) -> dict[str, Any]:
    repo = _existing(request, repo_key)
    lim = repo.limits
    if len(body.files) > lim["maxBatchFiles"] or sum(len(f.content.encode("utf-8", "replace")) for f in body.files) > lim["maxBatchBytes"]:
        raise HTTPException(status_code=413, detail=f"At most {lim['maxBatchFiles']} files and {lim['maxBatchBytes']} bytes per call")
    stored, rejected = await asyncio.to_thread(repo.store, [f.model_dump() for f in body.files])
    if any(r["reason"] == "user_quota" for r in rejected):
        # What was within the quota is stored; the rest is refused with a code the client can show.
        return JSONResponse(status_code=413, content={
            "detail": f"This would take your repositories past {lim['maxUserBytes']} bytes on this server, the most one person may keep. Remove a repository first.",
            "stored": stored, "rejected": rejected})
    return {"stored": stored, "rejected": rejected}


@router.post("/{repo_key}/commit", dependencies=[WRITE])
async def commit(request: Request, repo_key: str, body: CommitBody | None = None) -> JSONResponse:
    repo = _existing(request, repo_key)
    meta = await asyncio.to_thread(repo.commit)
    reasons = {k[:40]: min(v, 10_000_000) for k, v in (body.excluded if body else {}).items() if v > 0}
    meta = await asyncio.to_thread(repo.save_meta, clientExcluded=reasons)
    if meta.get("connectorId"):
        await _kick(request, repo, meta["connectorId"])
    return JSONResponse(status_code=202, content=await _status(request, repo))


@router.get("/{repo_key}", dependencies=[READ])
async def status(request: Request, repo_key: str) -> dict[str, Any]:
    return await _status(request, _existing(request, repo_key))


@router.get("", dependencies=[READ])
async def list_repos(request: Request) -> dict[str, Any]:
    org_id, user_id = _caller(request)
    root = store.store_base() / store._segment(org_id) / store._segment(user_id)
    keys = []
    if root.is_dir():
        for d in sorted(root.iterdir()):
            key = (await asyncio.to_thread(lambda d=d: json.loads((d / "repo.json").read_text("utf-8")) if (d / "repo.json").is_file() else {})).get("repoKey")
            if key:
                keys.append(key)
    return {"repos": [await _status(request, store.Repo(org_id, user_id, k)) for k in keys]}


@router.delete("/{repo_key}", dependencies=[Depends(require_scopes(OAuthScopes.CODE_WRITE))])
async def remove(request: Request, repo_key: str) -> JSONResponse:
    repo = _existing(request, repo_key)
    meta = await asyncio.to_thread(repo.meta)
    if meta.get("connectorId") and await _instance(request, meta["connectorId"]) is not None:
        await delete_connector_instance(meta["connectorId"], request, await get_graph_provider(request))
    try:
        await asyncio.to_thread(repo.destroy)
    except store.StoreError as exc:
        raise HTTPException(status_code=500, detail="The repository was removed from the index, but its files could not be removed from the server") from exc
    return JSONResponse(status_code=202, content={"repoKey": repo_key, "state": "deleting"})
