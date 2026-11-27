"""The proxy's binding table in memory: the ``EgressBindings`` adapter the proxy itself serves."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.engines.attempt_labels import bound_attempt
from harness_core.sandbox.errors import InvalidSandboxSpec, SandboxRefusal

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.sandbox.egress.types import EgressBinding, IssuedToken

PLACEHOLDER_IN_USE = "placeholder_in_use"
# A sandbox an incident stop revoked: it is never bound again, whoever asks (a broker's resync included).
EGRESS_REVOKED = "egress_revoked"
# The instance-wide stop is on: no sandbox serving a harness run is bound.
KILL_SWITCH = "kill_switch"
# An operator's egress stop (ADR-OPS-01 D4.3) the proxy holds covers this sandbox's run: it is not bound while held.
EGRESS_STOPPED = "egress_stopped"
ORG_LABEL = "orgId"
SESSION_LABEL = "sessionId"


@dataclass(frozen=True, slots=True)
class Revocation:
    """The live bindings an incident stop ends: every binding serving a harness run attempt, or only one org's, or
    one session's. A binding that names no run attempt (the standard chat's code sandbox, where the stop sends new
    chats) is never covered."""

    org_id: str | None = None
    session_id: str | None = None

    def covers(self, binding: EgressBinding) -> bool:
        if bound_attempt(binding.labels) is None:
            return False
        return (self.org_id is None or binding.labels.get(ORG_LABEL) == self.org_id) and (
            self.session_id is None or binding.labels.get(SESSION_LABEL) == self.session_id
        )


EVERY_RUN = Revocation()


def rebound(previous: EgressBinding | None, binding: EgressBinding) -> bool:
    """Whether ``binding`` re-binds a live sandbox for someone else: other labels (another run attempt) or other
    token routes. A token issued for the previous binding is not this one's (SIGNFIX: a kept sign-in sandbox's next
    message carried the last run's tool token)."""
    return previous is not None and (
        previous.labels != binding.labels or previous.policy.token_routes != binding.policy.token_routes
    )


class InMemoryEgressBindings:
    """``kill_switch``: the instance-wide stop (``AGENT_HARNESS_KILL_SWITCH``) was on when the proxy started."""

    def __init__(self, *, kill_switch: bool = False) -> None:
        self.kill_switch = kill_switch
        self._stops: frozenset[Revocation] = frozenset()
        self._revoked: set[str] = set()
        self._by_sandbox: dict[str, EgressBinding] = {}
        self._by_proxy_address: dict[str, str] = {}
        self._placeholder_owner: dict[str, str] = {}
        self._on_unregister: list[Callable[[str], None]] = []
        self._on_rebound: list[Callable[[str], None]] = []

    def on_unregister(self, callback: Callable[[str], None]) -> None:
        self._on_unregister.append(callback)

    def on_rebound(self, callback: Callable[[str], None]) -> None:
        """``callback(sandbox_id)`` when a live sandbox is bound again for someone else (``rebound``); its
        connections stay up, unlike ``on_unregister``."""
        self._on_rebound.append(callback)

    async def register(self, binding: EgressBinding) -> None:
        self.check(binding)
        _ = self.apply(binding)

    def check(self, binding: EgressBinding) -> None:
        """Refuse a binding whose placeholder another live sandbox holds, a revoked sandbox, and while the kill switch
        is on a sandbox serving a run; changes nothing."""
        if binding.sandbox_id in self._revoked:
            raise SandboxRefusal("this sandbox's egress was revoked", reason=EGRESS_REVOKED)
        if self.kill_switch and EVERY_RUN.covers(binding):
            raise SandboxRefusal("the agent harness kill switch is on", reason=KILL_SWITCH)
        if any(stop.covers(binding) for stop in self._stops):
            raise SandboxRefusal("an operator stopped this run's egress", reason=EGRESS_STOPPED)
        clashes = [
            p for p in binding.placeholders() if self._placeholder_owner.get(p, binding.sandbox_id) != binding.sandbox_id
        ]
        if clashes:
            raise InvalidSandboxSpec("a placeholder is held by another live sandbox", reason=PLACEHOLDER_IN_USE)

    def apply(self, binding: EgressBinding) -> str | None:
        """Bind a checked binding; the id of a stale binding it replaced on the same address, if any."""
        # The broker hands out each address once; a binding still on it is stale (a release
        # whose unregister was lost), and keeping it would strand the address.
        stale = self._by_proxy_address.get(binding.proxy_address)
        replaced = stale if stale is not None and stale != binding.sandbox_id else None
        if replaced is not None:
            self._forget(replaced, notify=True)
        previous = self._by_sandbox.get(binding.sandbox_id)
        self._forget(binding.sandbox_id, notify=False)
        self._by_sandbox[binding.sandbox_id] = binding
        self._by_proxy_address[binding.proxy_address] = binding.sandbox_id
        for placeholder in binding.placeholders():
            self._placeholder_owner[placeholder] = binding.sandbox_id
        if rebound(previous, binding):
            for callback in self._on_rebound:
                callback(binding.sandbox_id)
        return replaced

    async def unregister(self, sandbox_id: str) -> None:
        self.discard(sandbox_id)

    def discard(self, sandbox_id: str) -> None:
        """``unregister`` for synchronous teardown paths in the same process."""
        self._forget(sandbox_id, notify=True)

    async def bound(self) -> frozenset[str]:
        return frozenset(self._by_sandbox)

    async def revoke(self, revocation: Revocation) -> tuple[str, ...]:
        """Unbind every live binding ``revocation`` covers, for good: their tokens and connections end now, and the
        same sandbox is refused if anyone registers it again. The revoked sandbox ids, sorted."""
        return self.revoke_now(revocation)

    def revoke_now(self, revocation: Revocation) -> tuple[str, ...]:
        revoked = tuple(sorted(b.sandbox_id for b in self._by_sandbox.values() if revocation.covers(b)))
        for sandbox_id in revoked:
            self.mark_revoked(sandbox_id)
            self._forget(sandbox_id, notify=True)
        return revoked

    def stops(self) -> frozenset[Revocation]:
        """The operator egress stops held now; the start-up kill switch is not one of them."""
        return self._stops

    async def hold_stops(self, stops: frozenset[Revocation]) -> tuple[str, ...]:
        """Hold ``stops`` in place of the last list and revoke every live binding one covers; the revoked ids, sorted.

        Every held stop is applied again, not only a new one: a registration that passed ``check`` while the list was
        being replaced is ended by the next call. A cleared stop binds new sandboxes again; revoked ones stay revoked."""
        self._stops = frozenset(stops)
        revoked: set[str] = set()
        for stop in self._stops:
            revoked.update(await self.revoke(stop))
        return tuple(sorted(revoked))

    def mark_revoked(self, sandbox_id: str) -> None:
        self._revoked.add(sandbox_id)

    def revoked(self) -> frozenset[str]:
        return frozenset(self._revoked)

    def _forget(self, sandbox_id: str, *, notify: bool) -> None:
        binding = self._by_sandbox.pop(sandbox_id, None)
        if binding is None:
            return
        if self._by_proxy_address.get(binding.proxy_address) == sandbox_id:
            del self._by_proxy_address[binding.proxy_address]
        for placeholder in binding.placeholders():
            if self._placeholder_owner.get(placeholder) == sandbox_id:
                del self._placeholder_owner[placeholder]
        if notify:
            for callback in self._on_unregister:
                callback(sandbox_id)

    def for_local_address(self, address: str) -> EgressBinding | None:
        sandbox_id = self._by_proxy_address.get(address)
        return self._by_sandbox.get(sandbox_id) if sandbox_id is not None else None

    def get(self, sandbox_id: str) -> EgressBinding | None:
        return self._by_sandbox.get(sandbox_id)

    def placeholder_owner(self, placeholder: str) -> str | None:
        return self._placeholder_owner.get(placeholder)

    def proxy_addresses(self) -> frozenset[str]:
        return frozenset(self._by_proxy_address)

    def held(self) -> tuple[EgressBinding, ...]:
        return tuple(self._by_sandbox.values())

    async def record_grant(self, sandbox_id: str, credential_ref: str, token: IssuedToken) -> None:
        """A token the proxy obtained for a bound sandbox; kept only by a durable table."""

    def restored_grants(self) -> tuple[RestoredGrant, ...]:
        """Tokens a durable table brought back from before a restart."""
        return ()


@dataclass(frozen=True, slots=True)
class RestoredGrant:
    sandbox_id: str
    credential_ref: str
    token: IssuedToken = field(repr=False)


__all__ = [
    "EGRESS_REVOKED",
    "EGRESS_STOPPED",
    "EVERY_RUN",
    "KILL_SWITCH",
    "PLACEHOLDER_IN_USE",
    "InMemoryEgressBindings",
    "RestoredGrant",
    "Revocation",
    "rebound",
]
