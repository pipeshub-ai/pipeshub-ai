"""One push a person allowed (GIT-FLOW under WP-63's rule): a placeholder that is good for exactly that push.

An engine's own git fetches its repository with a read credential and can never push: the agent phase's git rule has
no push branch. When a person approves ``repository_push``, the worker mints a fresh placeholder, seals the push
credential for it, and registers a grant here (``POST /v1/push-grants``): this sandbox, this host and repository,
this branch, for ``ttl_s``. The push runs in the sandbox with that placeholder; only this proxy swaps it for the
credential, so no process ever holds a usable token. The grant is spent by the push's ``git-receive-pack``
request: the same placeholder again (a replay), after its time, from another sandbox, at another repository or to
another branch is refused.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.errors import EgressRefused
from harness_core.sandbox.egress.git import (
    RECEIVE_PACK,
    branch_ok,
    git_service,
    repository_ok,
)
from harness_core.sandbox.egress.policy import basic_password, normalize_host
from harness_core.sandbox.egress.sealed import PLACEHOLDER_PREFIX, is_sealed
from harness_core.sandbox.types import CredentialInjection, TokenRoute

if TYPE_CHECKING:
    from harness_core.clock import Clock
    from harness_core.sandbox.egress.types import EgressBinding, ProxyRequest, Target
    from harness_core.sandbox.types import GitAccess

# A person's approval is fresh: the push runs right after it, so a grant lives minutes at most.
DEFAULT_TTL_S: Final = 120
MAX_TTL_S: Final = 600
MAX_GRANTS_PER_SANDBOX: Final = 8


class PushGrantInvalid(ValueError):
    """A grant the proxy will not hold: its fields are malformed, or it names what the sandbox's binding does not."""


@dataclass(slots=True)
class PushGrant:
    placeholder: str
    sandbox_id: str
    host: str
    repository: str
    branch: str
    credential_ref: str
    expires_at: float
    spent: bool = False


class PushGrants:
    """The proxy's live grants, by placeholder; in memory only (a proxy that restarts drops them: the worker asks
    for a new one)."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._grants: dict[str, PushGrant] = {}

    def issue(
        self,
        binding: EgressBinding,
        *,
        placeholder: str,
        host: str,
        repository: str,
        branch: str,
        credential_ref: str,
        ttl_s: int = DEFAULT_TTL_S,
    ) -> PushGrant:
        """Holds a grant for one push from ``binding``'s sandbox to the repository its git rule names."""
        name = normalize_host(host)
        if not placeholder.startswith(PLACEHOLDER_PREFIX) or len(placeholder) < len(PLACEHOLDER_PREFIX) + 32:
            raise PushGrantInvalid("a push placeholder is a fresh one the proxy has never seen")
        if placeholder in self._grants or placeholder in binding.placeholders():
            raise PushGrantInvalid("a push placeholder is used once")
        if not repository_ok(repository) or not branch_ok(branch) or not is_sealed(credential_ref):
            raise PushGrantInvalid("a push grant names one repository, one branch and a sealed credential")
        if not 1 <= ttl_s <= MAX_TTL_S:
            raise PushGrantInvalid(f"a push grant lives at most {MAX_TTL_S} s")
        if not any(
            normalize_host(d.host) == name and d.git is not None and d.git.repository == repository
            for d in binding.policy.destinations
        ):
            raise PushGrantInvalid("the sandbox's git rule does not name that repository")
        self._expire()
        if sum(1 for g in self._grants.values() if g.sandbox_id == binding.sandbox_id) >= MAX_GRANTS_PER_SANDBOX:
            raise PushGrantInvalid("too many push grants for this sandbox")
        grant = PushGrant(
            placeholder, binding.sandbox_id, name, repository, branch, credential_ref, self._clock.monotonic() + ttl_s
        )
        self._grants[placeholder] = grant
        return grant

    def is_grant(self, value: str) -> bool:
        return value in self._grants

    def admit(
        self, request: ProxyRequest, target: Target, binding: EgressBinding, access: GitAccess
    ) -> tuple[GitAccess, TokenRoute] | None:
        """The git rule and credential route a request presenting a grant's placeholder runs under; ``None`` when it
        presents none. A ``git-receive-pack`` request spends the grant."""
        presented = [basic_password(v) for v in request.header_values("authorization")]
        found = [self._grants[p] for p in presented if p is not None and p in self._grants]
        if not found:
            return None
        grant = found[0]
        if len(presented) != 1 or grant.sandbox_id != binding.sandbox_id:
            raise EgressRefused(R.PLACEHOLDER_FOREIGN, "placeholder belongs to another sandbox")
        if grant.spent or self._clock.monotonic() > grant.expires_at:
            raise EgressRefused(R.TOKEN_REPLAYED, "this push was already made or its approval has expired")
        if target.host != grant.host or access.repository != grant.repository:
            raise EgressRefused(R.GIT_OPERATION_NOT_ALLOWED, "the push was approved for another repository")
        granted = replace(access, push_branch=grant.branch)
        service = git_service(request.method.upper(), target.path, granted)
        if service != RECEIVE_PACK:
            raise EgressRefused(R.GIT_OPERATION_NOT_ALLOWED, "an approved push's placeholder only pushes")
        if request.method.upper() == "POST":
            grant.spent = True
        return granted, TokenRoute(grant.placeholder, grant.host, grant.credential_ref, CredentialInjection.BASIC)

    def drop(self, sandbox_id: str) -> None:
        for placeholder in [p for p, g in self._grants.items() if g.sandbox_id == sandbox_id]:
            del self._grants[placeholder]

    def _expire(self) -> None:
        now = self._clock.monotonic()
        for placeholder in [p for p, g in self._grants.items() if g.expires_at < now - MAX_TTL_S]:
            del self._grants[placeholder]


__all__ = ["DEFAULT_TTL_S", "MAX_TTL_S", "PushGrant", "PushGrantInvalid", "PushGrants"]
