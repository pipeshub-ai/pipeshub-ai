"""MCP servers' tool lists, kept between chats, so a turn doesn't connect to every attached server
just to learn what it offers.

Two kinds of key (`mcp_server_constants`):

- a **pointer** per instance and sign-in (`credential_owner_id`): which catalog that sign-in's own
  discovery produced, against which server target and which credential record;
- a **blob** per catalog, keyed by its content hash, so identical catalogs are stored once.

A reader only follows its own pointer, or the instance's `_public` one when the server declared its
list the same for everyone (and an admin set the instance up). A blob is reached only through a
pointer whose discovery produced exactly those bytes, so nothing crosses between sign-ins.

Nothing here raises: a store that doesn't answer is a miss, and a write that fails is logged.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from pydantic import Field, ValidationError

from app.agents.constants.mcp_server_constants import (
    get_mcp_tool_catalog_blob_path,
    get_mcp_tool_catalog_pointer_path,
)
from app.agents.mcp.models import MCPCamelModel
from app.agents.mcp.service import credential_owner_id, is_personal
from app.utils.env_utils import env_int

if TYPE_CHECKING:
    from app.agents.mcp.models import MCPServerConfig, MCPToolInfo
    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

TTL_ENV = "MCP_TOOL_CACHE_TTL_SECONDS"
STALE_TTL_ENV = "MCP_TOOL_CACHE_STALE_TTL_SECONDS"
DEFAULT_STALE_TTL_SECONDS = 15 * 60
DEFAULT_TTL_SECONDS = 86400
# The owner key of a list the server says is the same for every caller.
PUBLIC_OWNER = "_public"
# etcd takes 1.5 MB per request; encryption and base64 add about 40%.
MAX_CATALOG_BYTES = 512 * 1024


class CachedTool(MCPCamelModel):
    name: str
    description: Optional[str] = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    annotations: Optional[dict[str, Any]] = None


class CachedCatalog(MCPCamelModel):
    tools: list[CachedTool]
    instructions: Optional[str] = None
    # Not part of the digest: when this copy of the blob was written.
    stored_at: float = 0.0


class CatalogPointer(MCPCamelModel):
    digest: str
    target: str
    credential_stamp: Optional[int] = None
    discovered_at: float
    expires_at: float
    tool_count: int


@dataclass(frozen=True)
class CacheHit:
    catalog: CachedCatalog
    pointer: CatalogPointer
    # The pointer it came from: the sign-in's own, or `PUBLIC_OWNER`.
    owner_key: str


def ttl_seconds() -> int:
    """`MCP_TOOL_CACHE_TTL_SECONDS`, 24 h by default; 0 turns the cache off."""
    return env_int(TTL_ENV, DEFAULT_TTL_SECONDS, lo=0) or 0


def stale_ttl_seconds() -> int:
    """How long a list a server calls "immediately stale" (`ttlMs: 0`) is kept:
    `MCP_TOOL_CACHE_STALE_TTL_SECONDS`, 15 minutes by default; 0 doesn't keep it."""
    return env_int(STALE_TTL_ENV, DEFAULT_STALE_TTL_SECONDS, lo=0) or 0


def enabled() -> bool:
    return ttl_seconds() > 0


def owner_key(instance: dict[str, Any], owner_id: str) -> str:
    """The sign-in a pointer belongs to: the shared slot for a shared admin credential, otherwise
    the user or agent key."""
    return credential_owner_id(instance, owner_id)


def public_allowed(instance: dict[str, Any]) -> bool:
    """A server's claim that its list is the same for everyone is trusted only for an org
    instance, which an admin set up. A personal one is run by whoever added it."""
    return not is_personal(instance)


def target_fingerprint(config: "MCPServerConfig") -> str:
    """What the server is and how it's reached: a template release or an edit that changes it
    makes every pointer to the old target a miss."""
    target = [
        config.type_id, config.transport.value, config.url, config.command, list(config.args or []),
        config.auth_mode.value, config.header_name, bool(config.use_admin_auth),
    ]
    return hashlib.sha256(json.dumps(target, separators=(",", ":")).encode()).hexdigest()


def credential_stamp(auth: Optional[dict[str, Any]]) -> Optional[int]:
    """When the credential record was created (`connectedAt`). A reconnect or a new token makes a
    new record, so a pointer discovered with the old one is a miss; a token refresh keeps it."""
    stamp = auth.get("connectedAt") if isinstance(auth, dict) else None
    return stamp if isinstance(stamp, int) else None


def catalog_from_tools(tools: "list[MCPToolInfo]", instructions: Optional[str]) -> CachedCatalog:
    return CachedCatalog(
        tools=[
            CachedTool(name=t.name, description=t.description, input_schema=t.input_schema, annotations=t.annotations)
            for t in tools
        ],
        instructions=instructions,
    )


def tool_listing(catalog: CachedCatalog) -> list[dict[str, Any]]:
    """The catalog's tools as a listing `discovery.tool_infos_from_listing` reads."""
    return [tool.model_dump(by_alias=True, exclude_none=True) for tool in catalog.tools]


def digest(catalog: CachedCatalog) -> str:
    content = {
        "tools": sorted((t.model_dump(by_alias=True) for t in catalog.tools), key=lambda t: t["name"]),
        "instructions": catalog.instructions,
    }
    return hashlib.sha256(_canonical(content).encode()).hexdigest()


def _canonical(value: Any) -> str:  # noqa: ANN401
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


async def read(
    config_service: "ConfigurationService",
    *,
    instance: dict[str, Any],
    config: "MCPServerConfig",
    owner_id: str,
    auth: Optional[dict[str, Any]],
) -> Optional[CacheHit]:
    """The cached catalog this sign-in may use for the instance, or None."""
    if not enabled():
        return None
    keys = [owner_key(instance, owner_id)]
    if public_allowed(instance):
        keys.insert(0, PUBLIC_OWNER)
    try:
        pointers = await asyncio.gather(*(
            config_service.get_config(
                get_mcp_tool_catalog_pointer_path(config.id, key), default=None, use_cache=False, keep_in_cache=False,
            )
            for key in keys
        ))
        target, stamp, now = target_fingerprint(config), credential_stamp(auth), time.time()
        for key, raw in zip(keys, pointers):
            pointer = _valid_pointer(raw)
            if pointer is None or pointer.target != target or pointer.expires_at <= now:
                continue
            # A public list doesn't depend on whose credentials discovered it.
            if key != PUBLIC_OWNER and pointer.credential_stamp != stamp:
                continue
            catalog = await _read_blob(config_service, pointer.digest)
            if catalog is not None:
                return CacheHit(catalog=catalog, pointer=pointer, owner_key=key)
    except Exception as e:
        logger.warning(f"MCP tool cache: couldn't read the cached tools of {config.id}: {e}")
    return None


async def _read_blob(config_service: "ConfigurationService", catalog_digest: str) -> Optional[CachedCatalog]:
    raw = await config_service.get_config(
        get_mcp_tool_catalog_blob_path(catalog_digest), default=None, use_cache=False, keep_in_cache=False,
    )
    if not isinstance(raw, dict):
        return None
    try:
        catalog = CachedCatalog.model_validate(raw)
    except ValidationError:
        return None
    # A blob that doesn't hash to its own key is corrupt.
    return catalog if digest(catalog) == catalog_digest else None


def _valid_pointer(raw: Any) -> Optional[CatalogPointer]:  # noqa: ANN401
    if not isinstance(raw, dict):
        return None
    try:
        return CatalogPointer.model_validate(raw)
    except ValidationError:
        return None


def entry_ttl(server_ttl_seconds: Optional[float]) -> int:
    """How long a new entry lives: ours, or the server's own hint when shorter. 0 means don't
    cache: a store TTL of 0 would mean no expiry at all.

    A hint of exactly 0 is the 2026-07-28 default, sent by every server that set nothing
    ("immediately stale"). Such a list is kept briefly: a turn that reads it lists again before
    its first call, and a tool whose arguments or hints changed isn't called (`mcp_session`)."""
    ttl = float(ttl_seconds())
    if server_ttl_seconds == 0:
        server_ttl_seconds = float(stale_ttl_seconds())
    if server_ttl_seconds is not None:
        ttl = min(ttl, server_ttl_seconds)
    return math.ceil(ttl) if ttl >= 1 else 0


async def write(
    config_service: "ConfigurationService",
    *,
    instance: dict[str, Any],
    config: "MCPServerConfig",
    owner_id: str,
    auth: Optional[dict[str, Any]],
    catalog: CachedCatalog,
    server_ttl_seconds: Optional[float] = None,
    public: bool = False,
) -> Optional[CatalogPointer]:
    """Remembers what this sign-in's discovery found. Returns the pointer written, or None when
    nothing was cached (off, empty, too big, the server said not to, or the store failed)."""
    if not enabled() or not catalog.tools:
        return None
    sign_in = owner_key(instance, owner_id)
    ttl = entry_ttl(server_ttl_seconds)
    try:
        if ttl == 0:
            # The server asked for no caching: what an earlier discovery left mustn't be served.
            await forget(config_service, instance_id=config.id, owner_key=sign_in)
            if public_allowed(instance):
                await forget(config_service, instance_id=config.id, owner_key=PUBLIC_OWNER)
            return None
        catalog_digest = digest(catalog)
        now = time.time()
        if not await _blob_fresh(config_service, catalog_digest, now, ttl):
            blob = catalog.model_copy(update={"stored_at": now}).model_dump(by_alias=True)
            if len(_canonical(blob).encode()) > MAX_CATALOG_BYTES:
                logger.info(f"MCP tool cache: the tools of {config.id} are too large to cache; it stays live")
                return None
            # The blob outlives every pointer written while it is fresh.
            if not await config_service.set_config(
                get_mcp_tool_catalog_blob_path(catalog_digest), blob, ttl_seconds=2 * ttl, keep_in_cache=False,
            ):
                return None
        pointer = CatalogPointer(
            digest=catalog_digest, target=target_fingerprint(config), credential_stamp=credential_stamp(auth),
            discovered_at=now, expires_at=now + ttl, tool_count=len(catalog.tools),
        )
        written_for = PUBLIC_OWNER if public and public_allowed(instance) else sign_in
        if not await config_service.set_config(
            get_mcp_tool_catalog_pointer_path(config.id, written_for), pointer.model_dump(by_alias=True),
            ttl_seconds=ttl, keep_in_cache=False,
        ):
            return None
        if written_for != PUBLIC_OWNER and public_allowed(instance):
            # A server that went from public to private must not keep being served the public list.
            await forget(config_service, instance_id=config.id, owner_key=PUBLIC_OWNER)
        return pointer
    except Exception as e:
        logger.warning(f"MCP tool cache: couldn't save the tools of {config.id}: {e}")
        return None


async def _blob_fresh(config_service: "ConfigurationService", catalog_digest: str, now: float, ttl: int) -> bool:
    """Whether the blob is stored and will outlive a pointer written now."""
    raw = await config_service.get_config(
        get_mcp_tool_catalog_blob_path(catalog_digest), default=None, use_cache=False, keep_in_cache=False,
    )
    stored_at = raw.get("storedAt") if isinstance(raw, dict) else None
    return isinstance(stored_at, (int, float)) and now - stored_at < ttl


def needs_refresh(pointer: CatalogPointer) -> bool:
    """Past half its life: a listing that found the same tools rewrites it."""
    return time.time() > pointer.discovered_at + (pointer.expires_at - pointer.discovered_at) / 2


async def forget(config_service: "ConfigurationService", *, instance_id: str, owner_key: str) -> None:
    """Drops one pointer. Read first: deleting a missing key logs an error."""
    path = get_mcp_tool_catalog_pointer_path(instance_id, owner_key)
    try:
        if await config_service.get_config(path, default=None, use_cache=False, keep_in_cache=False) is not None:
            await config_service.delete_config(path)
    except Exception as e:
        logger.warning(f"MCP tool cache: couldn't forget the cached tools of {instance_id}: {e}")
