"""A legacy chat request's resolved turn: verified once, before the request reads anything.

No header: today's reads, unless the policy store is ``on`` (the documents those reads use are no longer
written), where a chat request without one is refused. A header that does not verify is refused and the request
is never served from the reads.
"""

from __future__ import annotations

from contextlib import aclosing
from typing import TYPE_CHECKING, Final, Protocol, cast

from app.core.org_context import bound_org_id
from app.harness.gateway.keys import ConfiguredGatewayKeys
from app.harness.legacy.errors import ResolvedTurnRefused
from app.harness.legacy.resolved_turn import (
    RESOLVED_TURN_HEADER,
    ResolvedTurn,
    ResolvedTurnVerifier,
    binding_owner,
)
from app.harness.policy_documents import (
    ENV_POLICY_STORE,
    LIVE,
    InvalidPolicyStoreMode,
    policy_documents,
)
from app.services.featureflag.platform_settings import bind_pinned_stages
from harness_core.clock import SystemClock
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Mapping

    from app.config.configuration_service import ConfigurationService

PLACEHOLDER_AGENT_ID: Final = "agentIdPlaceholder"
POLICY_STORE_ENV: Final = ENV_POLICY_STORE
_VERIFIER: Final = "resolved_turn_verifier"
_LOG: Final = HarnessLog.named("app.harness.legacy")


class _Container(Protocol):
    def config_service(self) -> ConfigurationService: ...


class _App(Protocol):
    state: object
    container: _Container


class LegacyChatRequest(Protocol):
    """What this reads of a Starlette ``Request``: its headers, its verified identity and the process's state."""

    @property
    def headers(self) -> Mapping[str, str]: ...

    @property
    def state(self) -> object: ...

    @property
    def app(self) -> _App: ...


def _store_on() -> bool:
    """Whether the policy store is the record: then a request without a header is refused, never read from KV."""
    return policy_documents() == LIVE


def resolved_turn_verifier(request: LegacyChatRequest) -> ResolvedTurnVerifier:
    """The process's verifier, built at the first header: its key source caches the published keys."""
    state = request.app.state
    verifier = getattr(state, _VERIFIER, None)
    if not isinstance(verifier, ResolvedTurnVerifier):
        verifier = ResolvedTurnVerifier(ConfiguredGatewayKeys(request.app.container.config_service()), SystemClock())
        setattr(state, _VERIFIER, verifier)
    return verifier


def _identity(request: LegacyChatRequest) -> tuple[str, str]:
    """The org the auth middleware bound and the user it verified, never the body's."""
    user: object = getattr(request.state, "user", None)
    claims = cast("dict[str, object]", user) if isinstance(user, dict) else {}
    user_id = claims.get("userId")
    return (bound_org_id() or "", user_id if isinstance(user_id, str) else "")


async def resolved_turn_for(
    request: LegacyChatRequest, *, agent_id: str | None, conversation_id: str | None
) -> ResolvedTurn | None:
    """The turn Node resolved for this request, or ``None`` when it sent none and today's reads still apply.

    ``agent_id`` is the route's (the placeholder and ``None`` are the assistant); ``conversation_id`` the body's.
    Raises ``ResolvedTurnRefused``, logged here once with the reason and the request's ids, never the token."""
    token = request.headers.get(RESOLVED_TURN_HEADER)
    agent_key = None if agent_id in (None, PLACEHOLDER_AGENT_ID) else agent_id
    org_id, user_id = _identity(request)
    try:
        # A real request's header is a string; anything else is a request built without one.
        if not isinstance(token, str):
            if _store_on():
                raise ResolvedTurnRefused("resolved_required")
            return None
        if not org_id or not user_id:
            raise ResolvedTurnRefused("resolved_subject_mismatch")
        turn = await resolved_turn_verifier(request).verify(
            token, org_id=org_id, user_id=user_id, agent_key=agent_key, conversation_id=conversation_id
        )
    except ResolvedTurnRefused as refused:
        _LOG.warning(
            "legacy.resolved_refused",
            reasonCode=refused.reason_code,
            orgId=org_id or None,
            userId=user_id or None,
            agentKey=agent_key,
            conversationId=conversation_id,
        )
        raise
    _LOG.info(
        "legacy.resolved_turn",
        orgId=org_id,
        userId=user_id,
        agentKey=agent_key,
        conversationId=conversation_id,
        pinDigest=turn.pin.digest,
        headRevision=turn.pin.head,
        routingReason=turn.pin.routing.reason,
        access=turn.access_run,
        bindings=len(turn.bindings),
    )
    return turn


def bind_resolved_stages(turn: ResolvedTurn | None) -> None:
    """Every Labs flag read of this task answers from the turn's stages; a request without a turn reads as before."""
    if turn is not None:
        bind_pinned_stages(turn.stages)


def release_resolved_stages(turn: ResolvedTurn | None) -> None:
    """Undoes ``bind_resolved_stages``: the task may outlive the request (two requests on one task, a run started
    from it) and reads the settings again."""
    if turn is not None:
        bind_pinned_stages(None)


async def _with_stages(turn: ResolvedTurn, events: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
    # A server may drive the response body in another task than the handler's, or in the same one after it.
    bind_resolved_stages(turn)
    try:
        async with aclosing(events):
            async for event in events:
                yield event
    finally:
        release_resolved_stages(turn)


def with_resolved_stages(turn: ResolvedTurn | None, events: AsyncGenerator[str, None]) -> AsyncGenerator[str, None]:
    """``events`` with the turn's stages bound in the task that drives it, and unbound when it ends; a request
    without a turn gets ``events`` itself."""
    return events if turn is None else _with_stages(turn, events)


def bound_installations(turn: ResolvedTurn | None, owner_id: str, *, service_account: bool) -> frozenset[str]:
    """The installations whose credential the turn holds bound to the owner the route would look it up under."""
    if turn is None:
        return frozenset()
    return turn.bound_installations(binding_owner(owner_id, agent=service_account))


__all__ = [
    "PLACEHOLDER_AGENT_ID",
    "POLICY_STORE_ENV",
    "LegacyChatRequest",
    "InvalidPolicyStoreMode",
    "bind_resolved_stages",
    "bound_installations",
    "release_resolved_stages",
    "resolved_turn_for",
    "resolved_turn_verifier",
    "with_resolved_stages",
]
