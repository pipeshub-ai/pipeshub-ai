"""Client for the Node chat-content policy decision point (PDP).

Chat attachments and artifacts are readable by a non-uploader only when Node
says so *now*. Every failure mode (transport, timeout, non-200, malformed body)
is a deny: the PDP being down must never widen access.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal, Protocol

import aiohttp
from pydantic import BaseModel, StrictBool, StrictInt, ValidationError

from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import (
    DefaultEndpoints,
    TokenScopes,
    config_node_constants,
)
from app.utils.jwt import mint_service_token
from app.utils.request_context import inject_request_headers

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.config.configuration_service import ConfigurationService

logger = logging.getLogger(__name__)

__all__ = [
    "AiohttpPdpHttp",
    "ChatArtifactKind",
    "ChatContentCheck",
    "ChatContentPdp",
    "DenyAllPdpClient",
    "NodePdpClient",
    "PdpConnectError",
    "PdpHttp",
    "PdpHttpResponse",
    "get_node_pdp_client",
    "set_node_pdp_client",
]

CHECK_PATH = "/api/v1/authz/internal/check"
REQUEST_TIMEOUT_S = 2.0
CACHE_TTL_S = 30.0
CACHE_MAX_ENTRIES = 10_000
TOKEN_TTL = timedelta(seconds=60)
TOKEN_REUSE_S = 50.0
_TOKEN_CACHE_MAX = 1_000
_CONNECT_ATTEMPTS = 2


class ChatArtifactKind(BaseModel):
    visibility: str
    is_temporary: bool = False


class ChatContentCheck(BaseModel):
    user_id: str
    org_id: str
    resource_type: Literal["chatAttachment", "chatArtifact"]
    record_id: str
    owner_user_id: str
    conversation_id: str | None = None
    run_id: str | None = None
    kind: ChatArtifactKind | None = None
    acl_version: int | None = None

    def to_wire(self) -> dict[str, Any]:
        resource: dict[str, Any] = {
            "type": self.resource_type,
            "recordId": self.record_id,
            "ownerUserId": self.owner_user_id,
        }
        if self.conversation_id:
            resource["conversationId"] = self.conversation_id
        if self.run_id:
            resource["runId"] = self.run_id
        if self.kind is not None:
            resource["kind"] = {
                "visibility": self.kind.visibility,
                "isTemporary": self.kind.is_temporary,
            }
        return {
            "userId": self.user_id,
            "orgId": self.org_id,
            "action": "read",
            "resource": resource,
        }


class _CheckResponse(BaseModel):
    allow: StrictBool
    aclVersion: StrictInt | None = None


class PdpHttpResponse(BaseModel):
    status: int
    body: Any = None


class PdpConnectError(Exception):
    """The PDP could not be reached at all (safe to retry once)."""


class PdpHttp(Protocol):
    async def post_json(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout_s: float,
    ) -> PdpHttpResponse: ...


class AiohttpPdpHttp:
    """`PdpHttp` over the process-wide aiohttp session used for Node calls."""

    async def post_json(
        self, url: str, *, json: dict[str, Any], headers: dict[str, str], timeout_s: float,
    ) -> PdpHttpResponse:
        from app.modules.transformers.blob_storage import get_shared_session

        session = get_shared_session()
        try:
            async with session.post(
                url, json=json, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout_s),
            ) as resp:
                try:
                    body = await resp.json(content_type=None)
                except Exception:
                    body = None
                return PdpHttpResponse(status=resp.status, body=body)
        except aiohttp.ClientConnectorError as exc:
            raise PdpConnectError(str(exc)) from exc


class ChatContentPdp(Protocol):
    async def can_read_chat_content(self, req: ChatContentCheck) -> bool: ...


class DenyAllPdpClient:
    """Used when no PDP is wired (tests, misconfiguration): fail closed."""

    async def can_read_chat_content(self, req: ChatContentCheck) -> bool:
        return False


class NodePdpClient:
    def __init__(
        self,
        config_service: ConfigurationService,
        http: PdpHttp,
        *,
        clock: Callable[[], float] = time.monotonic,
        cache_ttl_s: float = CACHE_TTL_S,
        cache_max_entries: int = CACHE_MAX_ENTRIES,
        timeout_s: float = REQUEST_TIMEOUT_S,
    ) -> None:
        self._config = config_service
        self._http = http
        self._clock = clock
        self._ttl = cache_ttl_s
        self._max_entries = cache_max_entries
        self._timeout_s = timeout_s
        self._cache: OrderedDict[tuple, float] = OrderedDict()
        self._tokens: OrderedDict[tuple[str, str], tuple[str, float]] = OrderedDict()

    async def can_read_chat_content(self, req: ChatContentCheck) -> bool:
        # The cache is only valid where Node supplied the chat's aclVersion: a
        # bump changes the key, so a revocation is seen on the next request.
        # Preview and download paths carry no version and always ask Node.
        key = self._cache_key(req)
        if key is not None and self._cache_hit(key):
            return True

        allow, node_version = await self._ask_node(req)
        if allow and key is not None and self._version_still_current(req, node_version):
            self._cache_put(key)
        return allow

    @staticmethod
    def _cache_key(req: ChatContentCheck) -> tuple | None:
        if req.acl_version is None:
            return None
        return (req.org_id, req.user_id, req.record_id, req.conversation_id, req.acl_version)

    @staticmethod
    def _version_still_current(req: ChatContentCheck, node_version: int | None) -> bool:
        return node_version is None or node_version == req.acl_version

    def _cache_hit(self, key: tuple) -> bool:
        expires = self._cache.get(key)
        if expires is None:
            return False
        if expires <= self._clock():
            del self._cache[key]
            return False
        self._cache.move_to_end(key)
        return True

    def _cache_put(self, key: tuple) -> None:
        # Only allows are cached: a stale deny would hide a file after consent
        # is granted, while a stale allow is bounded by the TTL and aclVersion.
        self._cache[key] = self._clock() + self._ttl
        self._cache.move_to_end(key)
        while len(self._cache) > self._max_entries:
            self._cache.popitem(last=False)

    async def _ask_node(self, req: ChatContentCheck) -> tuple[bool, int | None]:
        try:
            url, headers = await self._prepare(req)
        except Exception:
            logger.warning(
                "authz_pdp_error: cannot prepare PDP call record=%s", req.record_id, exc_info=True,
            )
            return False, None

        response: PdpHttpResponse | None = None
        for attempt in range(_CONNECT_ATTEMPTS):
            try:
                response = await asyncio.wait_for(
                    self._http.post_json(
                        url, json=req.to_wire(), headers=headers, timeout_s=self._timeout_s,
                    ),
                    timeout=self._timeout_s + 0.5,
                )
                break
            except PdpConnectError:
                if attempt + 1 < _CONNECT_ATTEMPTS:
                    continue
                logger.warning("authz_pdp_error: connect failed record=%s", req.record_id)
                return False, None
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning("authz_pdp_error: timeout record=%s", req.record_id)
                return False, None
            except Exception:
                logger.warning("authz_pdp_error: transport error record=%s", req.record_id)
                return False, None

        if response is None or response.status != HttpStatusCode.SUCCESS.value:
            logger.warning(
                "authz_pdp_error: status=%s record=%s",
                getattr(response, "status", None), req.record_id,
            )
            return False, None
        try:
            parsed = _CheckResponse.model_validate(response.body)
        except ValidationError:
            logger.warning("authz_pdp_error: malformed response record=%s", req.record_id)
            return False, None
        return parsed.allow, parsed.aclVersion

    async def _prepare(self, req: ChatContentCheck) -> tuple[str, dict[str, str]]:
        endpoints = await self._config.get_config(
            config_node_constants.ENDPOINTS.value, use_cache=True
        )
        base = ((endpoints or {}).get("cm") or {}).get(
            "endpoint", DefaultEndpoints.NODEJS_ENDPOINT.value
        )
        if not base:
            raise ValueError("Missing CM endpoint configuration")
        token = await self._token(req.org_id, req.user_id)
        headers = inject_request_headers({"Authorization": f"Bearer {token}"})
        return f"{str(base).rstrip('/')}{CHECK_PATH}", headers

    async def _token(self, org_id: str, user_id: str) -> str:
        key = (org_id, user_id)
        cached = self._tokens.get(key)
        now = self._clock()
        if cached is not None and cached[1] > now:
            return cached[0]
        secret_keys = await self._config.get_config(
            config_node_constants.SECRET_KEYS.value, use_cache=True
        )
        secret = (secret_keys or {}).get("scopedJwtSecret")
        if not secret:
            raise ValueError("Missing scoped JWT secret")
        token = mint_service_token(
            secret,
            {"userId": user_id, "orgId": org_id, "scopes": [TokenScopes.AUTHZ_CHECK.value]},
            ttl=TOKEN_TTL,
        )
        self._tokens[key] = (token, now + TOKEN_REUSE_S)
        self._tokens.move_to_end(key)
        while len(self._tokens) > _TOKEN_CACHE_MAX:
            self._tokens.popitem(last=False)
        return token


class _Installed:
    client: ChatContentPdp | None = None


_DENY_ALL = DenyAllPdpClient()


def set_node_pdp_client(client: ChatContentPdp | None) -> None:
    _Installed.client = client


def get_node_pdp_client() -> ChatContentPdp:
    return _Installed.client if _Installed.client is not None else _DENY_ALL
