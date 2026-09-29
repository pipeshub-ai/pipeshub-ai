"""The caller's live org role, as the Node identity service sees it.

Only Node can tell whether a token has been revoked since it was signed: a session
ended by signing out, a password change, a lockout or the user's deletion, or an
OAuth/PAT token withdrawn. OAuth/PAT tokens also carry no role claim, so their role
comes from here too. ``fetch_caller_role`` forwards the
caller's own credentials to ``GET /api/v1/users/me/role``, so the answer is always
about the caller: there is no user id a caller could steer, and no OAuth scope needed.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal, cast

import httpx

from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import DefaultEndpoints, config_node_constants
from app.utils.logger import create_logger

if TYPE_CHECKING:
    import ssl
    from collections.abc import Awaitable, Callable, Mapping

    from fastapi import Request

    from app.config.configuration_service import ConfigurationService

logger = create_logger(__name__)

CALLER_ROLE_PATH = "/api/v1/users/me/role"
_TIMEOUT_SECONDS = 5.0
# Node authenticates the caller from the Authorization header alone, so nothing else is
# sent; cookies in particular can carry a long-lived refresh token.
_FORWARDED_HEADERS = ("authorization",)

Role = Literal["admin", "member"]


def normalize_auth_role(role: object) -> Role:
    """Map a role claim to admin|member. Unknown/missing → member (fail closed)."""
    if isinstance(role, str) and role.strip().lower() == "admin":
        return "admin"
    return "member"


class CallerRoleStatus(Enum):
    VALID = "valid"  # Node authenticated the credentials and returned the role
    REJECTED = "rejected"  # Node refused them: expired, revoked, or the user is gone
    UNKNOWN = "unknown"  # Node could not answer; privileges fail closed


@dataclass(frozen=True)
class CallerRole:
    status: CallerRoleStatus
    role: Role = "member"

    @property
    def is_admin(self) -> bool:
        return self.status is CallerRoleStatus.VALID and self.role == "admin"


_UNKNOWN = CallerRole(CallerRoleStatus.UNKNOWN)


def _str_field(value: object, key: str) -> object:
    return cast("dict[str, object]", value).get(key) if isinstance(value, dict) else None


async def _nodejs_endpoint(config_service: ConfigurationService) -> str:
    try:
        endpoints = cast(
            "object",
            await config_service.get_config(config_node_constants.ENDPOINTS.value, use_cache=False),
        )
    except Exception as exc:
        logger.warning(
            "Could not read service endpoints (%s); using the default Node endpoint",
            type(exc).__name__,
        )
        endpoints = None
    endpoint = _str_field(_str_field(endpoints, "nodejs"), "endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        endpoint = DefaultEndpoints.NODEJS_ENDPOINT.value
    return endpoint.rstrip("/")


@functools.cache
def _ssl_context() -> ssl.SSLContext:
    # A client left to build its own loads the CA bundle each time: about 15 ms of
    # CPU that blocks the event loop, on a path every session request can now reach.
    return httpx.create_ssl_context()


def _forwarded_headers(headers: Mapping[str, str]) -> dict[str, str]:
    present = {name.lower(): value for name, value in headers.items()}
    return {name: present[name] for name in _FORWARDED_HEADERS if present.get(name)}


async def fetch_caller_role(
    request: Request, config_service: ConfigurationService
) -> CallerRole:
    """Ask Node for the role of whoever the request's credentials identify."""
    headers = _forwarded_headers(request.headers)
    if not headers.get("authorization"):
        return _UNKNOWN

    url = f"{await _nodejs_endpoint(config_service)}{CALLER_ROLE_PATH}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, verify=_ssl_context()) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        logger.warning("Caller role lookup failed: %s", type(exc).__name__)
        return _UNKNOWN

    if response.status_code == HttpStatusCode.UNAUTHORIZED.value:
        return CallerRole(CallerRoleStatus.REJECTED)
    if response.status_code != HttpStatusCode.OK.value:
        logger.warning("Caller role lookup returned HTTP %s", response.status_code)
        return _UNKNOWN
    try:
        body: object = response.json()
    except ValueError:
        logger.warning("Caller role lookup returned a non-JSON body")
        return _UNKNOWN
    return CallerRole(CallerRoleStatus.VALID, normalize_auth_role(_str_field(body, "role")))


class CallerRoleCache:
    """Node's recent answers about a token, keyed by the token's SHA-256.

    Concurrent lookups for one token share a single call to Node, and a definite
    answer is reused for ``ttl_seconds``: a page load fans out into many requests
    carrying the same token, and they should cost Node one lookup, not one each.
    The price is that a token Node starts refusing is still accepted here until
    the answer it gave last expires. An UNKNOWN answer is never kept, so the next
    request asks again.
    """

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._answers: OrderedDict[bytes, tuple[float, CallerRole]] = OrderedDict()
        self._in_flight: dict[bytes, asyncio.Task[CallerRole]] = {}

    def clear(self) -> None:
        self._answers.clear()
        self._in_flight.clear()

    async def get(self, token: str, lookup: Callable[[], Awaitable[CallerRole]]) -> CallerRole:
        key = hashlib.sha256(token.encode()).digest()
        cached = self._answers.get(key)
        if cached is not None:
            expires_at, answer = cached
            if self._clock() < expires_at:
                return answer
            del self._answers[key]

        task = self._in_flight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._look_up(key, lookup))
            self._in_flight[key] = task
        # One waiter's request being cancelled must not cancel the lookup the others share.
        return await asyncio.shield(task)

    async def _look_up(self, key: bytes, lookup: Callable[[], Awaitable[CallerRole]]) -> CallerRole:
        try:
            answer = await lookup()
        finally:
            self._in_flight.pop(key, None)
        if answer.status is not CallerRoleStatus.UNKNOWN:
            self._answers[key] = (self._clock() + self._ttl_seconds, answer)
            self._answers.move_to_end(key)
            while len(self._answers) > self._max_entries:
                self._answers.popitem(last=False)
        return answer
