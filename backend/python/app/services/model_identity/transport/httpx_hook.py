"""The httpx request hook that puts identity on a model request.

It runs on the request path of every model call, so it does no I/O of its own:
groups come from a prefetched handle, signing material from memory, and the
rendered ``HeaderSet`` is memoized on the call context. It never raises: a
failure in identity forwarding must not fail a model call.
"""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx

from app.services.model_identity import metrics
from app.services.model_identity.call_context import (
    ModelCallContext,
    current_call_context,
    current_llm_purpose,
)
from app.services.model_identity.errors import classify_gateway_error, record_refusal
from app.services.model_identity.forwarders.base import HeaderSet, IdentityForwarder
from app.services.model_identity.groups import EMPTY_MEMBERSHIP, GroupMembership
from app.services.model_identity.principal import service_principal
from app.services.model_identity.projector import IdentityProjector
from app.services.model_identity.settings import (
    DEFAULT_GOVERNANCE,
    ConnectionForwardingSettings,
)
from app.services.model_identity.workload import Workload

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

HOOK_MARKER = "_pipeshub_identity_hook"
_REFUSAL_STATUSES = frozenset({400, 401, 403, 422, 429})

# Re-render a little before a token actually expires so one never goes out stale.
_EXPIRY_SKEW_SECONDS = 5.0
_logged_errors: set[str] = set()


class IdentityHook:
    """Renders identity for one connection. Holds no per-request state."""

    def __init__(
        self,
        settings: ConnectionForwardingSettings,
        forwarder: IdentityForwarder,
        projector: IdentityProjector | None = None,
    ) -> None:
        self._settings = settings
        self._forwarder = forwarder
        self._projector = projector or IdentityProjector()

    def _wants_groups(self, ctx: ModelCallContext) -> bool:
        return (
            ctx.principal.is_user
            and ctx.governance.groups_enabled
            and ctx.groups is not None
            and self._forwarder.requires_groups(self._settings)
        )

    def _headers(self, ctx: ModelCallContext, groups: GroupMembership | None) -> HeaderSet:
        purpose = current_llm_purpose()
        key = (self._settings.fingerprint, purpose, groups)
        cached = ctx.memo.get(key)
        if cached is not None and (
            cached.expires_at is None or time.monotonic() < cached.expires_at - _EXPIRY_SKEW_SECONDS
        ):
            return cached
        identity = self._projector.project(ctx, ctx.governance, groups=groups, purpose=purpose)
        rendered = self._forwarder.render(identity, self._settings)
        ctx.memo[key] = rendered
        return rendered

    def _service_fallback(self, original: ModelCallContext) -> HeaderSet:
        ctx = ModelCallContext(principal=service_principal(), workload=Workload.SYSTEM)
        identity = self._projector.project(ctx, DEFAULT_GOVERNANCE, groups=EMPTY_MEMBERSHIP)
        rendered = self._forwarder.render(identity, self._settings)
        if not original.principal.is_user:
            return rendered
        # A person's request must never be moved onto the background service's
        # virtual key, whose team, budget and model list are not theirs.
        return HeaderSet(
            set=tuple((n, v) for n, v in rendered.set if n.lower() != "authorization"),
            remove=tuple(n for n in rendered.remove if n.lower() != "authorization"),
            expires_at=rendered.expires_at,
        )

    def _apply(self, request: httpx.Request, render: Callable[[], HeaderSet]) -> None:
        ctx = current_call_context()
        mode = self._settings.mode
        try:
            render().apply(request.headers)
            metrics.inc(metrics.IDENTITY_FORWARDED, mode, ctx.workload_class.value, "ok")
        except Exception as exc:
            metrics.inc(metrics.IDENTITY_FORWARDED, mode, ctx.workload_class.value, "error")
            _log_once(exc)
            with contextlib.suppress(Exception):
                self._service_fallback(ctx).apply(request.headers)

    def sync_hook(self, request: httpx.Request) -> None:
        def render() -> HeaderSet:
            ctx = current_call_context()
            groups = self._sync_groups(ctx)
            return self._headers(ctx, groups)

        self._apply(request, render)

    async def async_hook(self, request: httpx.Request) -> None:
        ctx = current_call_context()
        try:
            groups = await self._async_groups(ctx)
        except Exception as exc:
            # Cancellation is a BaseException and passes through.
            _log_once(exc)
            groups = None
        self._apply(request, lambda: self._headers(ctx, groups))

    def sync_response(self, response: httpx.Response) -> None:
        if response.status_code not in _REFUSAL_STATUSES:
            return
        try:
            response.read()
            self._mark_refusal(response)
        except Exception as exc:
            _log_once(exc)

    async def async_response(self, response: httpx.Response) -> None:
        if response.status_code not in _REFUSAL_STATUSES:
            return
        try:
            await response.aread()
            self._mark_refusal(response)
        except Exception as exc:
            _log_once(exc)

    @staticmethod
    def _mark_refusal(response: httpx.Response) -> None:
        refusal = classify_gateway_error(response.status_code, response.content, response.headers)
        if refusal is None:
            return
        record_refusal(refusal, current_call_context().workload_class.value)
        if not refusal.retryable:
            # The OpenAI and Anthropic SDKs both honour this: a budget or model-access
            # refusal comes back identical on every attempt, so retrying only delays the answer.
            response.headers["x-should-retry"] = "false"

    def _sync_groups(self, ctx: ModelCallContext) -> GroupMembership | None:
        if not ctx.principal.is_user:
            return None
        if not self._wants_groups(ctx):
            return EMPTY_MEMBERSHIP
        return ctx.groups.result_nowait()  # type: ignore[union-attr]

    async def _async_groups(self, ctx: ModelCallContext) -> GroupMembership | None:
        if not ctx.principal.is_user:
            return None
        if not self._wants_groups(ctx):
            return EMPTY_MEMBERSHIP
        return await ctx.groups.wait()  # type: ignore[union-attr]


def _log_once(exc: BaseException) -> None:
    name = type(exc).__name__
    if name in _logged_errors:
        return
    _logged_errors.add(name)
    logger.warning("identity forwarding failed (%s); sending the service identity", name, exc_info=exc)


def install_identity_hook(
    client: Any, settings: ConnectionForwardingSettings, forwarder: IdentityForwarder  # noqa: ANN401 - SDK clients and models are duck-typed across providers
) -> bool:
    """Add the hook to ``client``. False if it already has one.

    A client is instrumented once: a second install would render and apply the
    same headers twice, and two connections must not share one client.
    """
    if getattr(client, HOOK_MARKER, None) is not None:
        return False
    hook = IdentityHook(settings, forwarder)
    fn: Callable[..., Any] = hook.async_hook if isinstance(client, httpx.AsyncClient) else hook.sync_hook
    hooks = client.event_hooks
    hooks["request"] = [*hooks.get("request", []), fn]
    response_fn = hook.async_response if isinstance(client, httpx.AsyncClient) else hook.sync_response
    hooks["response"] = [*hooks.get("response", []), response_fn]
    client.event_hooks = hooks
    setattr(client, HOOK_MARKER, hook)
    return True


def has_identity_hook(client: Any) -> bool:  # noqa: ANN401 - SDK clients and models are duck-typed across providers
    return getattr(client, HOOK_MARKER, None) is not None


__all__ = ["IdentityHook", "has_identity_hook", "install_identity_hook"]
