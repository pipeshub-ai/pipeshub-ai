"""Builds and binds the call context at a request or event boundary.

Boundaries (a chat route, the indexing event loop, a health check) call
``call_scope`` and run their work inside it. Everything the hook needs that
takes I/O (the governance policy, the pseudonym key, a group lookup) is
fetched here, once, so the hook itself never waits on anything.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from app.services.model_identity import metrics
from app.services.model_identity.call_context import (
    ModelCallContext,
    _Binding,
    bind_call_context,
)
from app.services.model_identity.groups import (
    GroupMembershipResolver,
    GroupsHandle,
    NoopGroupResolver,
)
from app.services.model_identity.policy_store import IdentityPolicyStore
from app.services.model_identity.principal import Principal, PrincipalKind
from app.services.model_identity.settings import DEFAULT_GOVERNANCE
from app.services.model_identity.workload import Workload, WorkloadClass, workload_class
from app.utils.request_context import get_context

logger = logging.getLogger(__name__)


class MissingIdentityError(HTTPException):
    """An interactive call has no user, and the org's policy refuses to send it as the service.

    An ``HTTPException`` so a route's own ``except HTTPException: raise`` lets it
    through as a 403 instead of folding it into a 500.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=403, detail=detail)


class _ForwardingState:
    """Process-wide facts the boundaries use to skip work nothing will read."""

    def __init__(self) -> None:
        self.forwarding_enabled = False
        self.groups_wanted = False


_state = _ForwardingState()


def note_forwarding(*, requires_groups: bool) -> None:
    """Called when a connection starts forwarding identity."""
    _state.forwarding_enabled = True
    _state.groups_wanted = _state.groups_wanted or requires_groups


def forwarding_state() -> _ForwardingState:
    return _state


def reset_forwarding_state() -> None:
    global _state  # noqa: PLW0603 - a lazily built process-wide singleton
    _state = _ForwardingState()


class IdentityRuntime:
    def __init__(
        self,
        policy_store: IdentityPolicyStore | None,
        resolver: GroupMembershipResolver | None = None,
    ) -> None:
        self._policies = policy_store
        self._resolver: GroupMembershipResolver = resolver or NoopGroupResolver()

    async def context(
        self,
        principal: Principal,
        workload: Workload,
        *,
        request_id: str | None = None,
        conversation_id: str | None = None,
        agent_id: str | None = None,
        record_id: str | None = None,
        connector_id: str | None = None,
    ) -> ModelCallContext:
        governance = DEFAULT_GOVERNANCE
        pseudonym_key: bytes | None = None
        if self._policies is not None:
            governance = await self._policies.get(principal.org_id)
            if governance.pseudonymize:
                pseudonym_key = await self._policies.pseudonym_key()

        self._enforce_strict(principal, workload, governance.on_missing_identity)

        groups: GroupsHandle | None = None
        if (
            principal.is_user
            and governance.groups_enabled
            and _state.groups_wanted
            and not isinstance(self._resolver, NoopGroupResolver)
        ):
            groups = GroupsHandle(self._resolver, principal)
            groups.prefetch()

        if request_id is None:
            root = get_context()
            request_id = root.root_id if root is not None else None

        return ModelCallContext(
            principal=principal,
            workload=workload,
            request_id=request_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            record_id=record_id,
            connector_id=connector_id,
            groups=groups,
            governance=governance,
            pseudonym_key=pseudonym_key,
        )

    async def governance(self, org_id: str):  # noqa: ANN201 - IdentityGovernancePolicy
        if self._policies is None:
            return DEFAULT_GOVERNANCE
        return await self._policies.get(org_id)

    async def pseudonym_key(self) -> bytes | None:
        return None if self._policies is None else await self._policies.pseudonym_key()

    async def resolve_groups(self, principal: Principal, budget_seconds: float = 2.0):  # noqa: ANN201
        """One group lookup for ``principal``, waited for (an admin preview, not a model call)."""
        handle = GroupsHandle(self._resolver, principal, budget_seconds=budget_seconds)
        handle.prefetch()
        return await handle.wait(budget_seconds)

    @staticmethod
    def _enforce_strict(principal: Principal, workload: Workload, on_missing: str) -> None:
        # Checked here, before any model call, and not in the hook: an exception
        # in a transport hook surfaces as a retryable network error.
        if (
            on_missing == "block"
            and workload_class(workload) is WorkloadClass.INTERACTIVE
            and principal.kind is PrincipalKind.SERVICE
            and _state.forwarding_enabled
        ):
            metrics.inc(metrics.IDENTITY_MISSING, f"blocked_{workload.value}")
            raise MissingIdentityError(
                "this request has no signed-in user and the organisation's policy does not allow "
                "it to reach the model gateway as the service"
            )


_runtime: IdentityRuntime | None = None
_runtime_key: int | None = None


def get_identity_runtime(config_service: Any = None, container: Any = None) -> IdentityRuntime:  # noqa: ANN401 - an untyped dependency-injection object
    """The process runtime, built on first use from the config service and edition hook.

    Rebuilt if called with a different config service (tests, a restart in the
    same process). A caller that passes none keeps whatever runtime exists, so
    it cannot swap the configured policy store for an empty one.
    """
    global _runtime, _runtime_key
    key = id(config_service) if config_service is not None else None
    if _runtime is not None and (config_service is None or key == _runtime_key):
        return _runtime
    store = IdentityPolicyStore(config_service) if config_service is not None else None
    resolver = _edition_resolver(container)
    _runtime, _runtime_key = IdentityRuntime(store, resolver), key
    return _runtime


def set_identity_runtime(runtime: IdentityRuntime | None) -> None:
    global _runtime, _runtime_key
    _runtime, _runtime_key = runtime, None


def _edition_resolver(container: Any) -> GroupMembershipResolver:  # noqa: ANN401 - an untyped dependency-injection object
    try:
        from app import edition_services

        return edition_services.get_group_membership_resolver(container)
    except Exception:
        logger.warning("group resolver unavailable; no groups will be forwarded", exc_info=True)
        return NoopGroupResolver()


class call_scope:
    """``async with call_scope(principal, Workload.CHAT, config_service=cs): ...``

    Builds the context (fetching policy and starting any group lookup) and binds
    it for the block. Raises ``MissingIdentityError`` in strict mode.
    """

    def __init__(
        self,
        principal: Principal,
        workload: Workload,
        *,
        config_service: Any = None,  # noqa: ANN401 - an untyped dependency-injection object
        container: Any = None,  # noqa: ANN401 - an untyped dependency-injection object
        **metadata: str | None,
    ) -> None:
        self._principal = principal
        self._workload = workload
        self._config_service = config_service
        self._container = container
        self._metadata: dict[str, str | None] = metadata
        self._binding: _Binding | None = None

    async def __aenter__(self) -> ModelCallContext:
        runtime = get_identity_runtime(self._config_service, self._container)
        try:
            ctx = await runtime.context(self._principal, self._workload, **self._metadata)
        except MissingIdentityError:
            raise
        except Exception:
            # Identity is best effort around the call: a failure to prepare it must
            # not fail the request, and the call still carries a context.
            logger.warning("could not prepare the call context; using defaults", exc_info=True)
            ctx = ModelCallContext(principal=self._principal, workload=self._workload)
        self._binding = bind_call_context(ctx)
        return self._binding.__enter__()

    async def __aexit__(self, *exc_info: object) -> None:
        binding, self._binding = self._binding, None
        if binding is not None:
            binding.__exit__(*exc_info)


async def assert_identity_allowed(
    principal: Principal,
    workload: Workload,
    *,
    config_service: Any = None,  # noqa: ANN401 - an untyped dependency-injection object
    container: Any = None,  # noqa: ANN401 - an untyped dependency-injection object
) -> None:
    """Raise ``MissingIdentityError`` now if strict governance would refuse this call.

    For streaming routes, where a refusal has to be an HTTP error returned
    before the stream starts, not an event inside it.
    """
    governance = DEFAULT_GOVERNANCE
    runtime = get_identity_runtime(config_service, container)
    if runtime._policies is not None:
        try:
            governance = await runtime._policies.get(principal.org_id)
        except Exception:
            logger.warning("identity policy unavailable; not enforcing strict mode", exc_info=True)
    IdentityRuntime._enforce_strict(principal, workload, governance.on_missing_identity)
