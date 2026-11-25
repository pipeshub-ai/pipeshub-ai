"""A person's own sign-in to an engine inside their sandbox (SIGNIN, C-1): the engine-neutral port.

The engine's own login runs in the run's sandbox; PipesHub relays the sign-in link it prints (after checking its
host against the signed list) and the code the person pastes, straight to the engine's prompt, and keeps neither.
The login lives in that sandbox only. Between turns of one conversation the sandbox is kept for the same person,
conversation, workspace and engine profile (``KeptSignIn``, SIGNIN-5) until it is idle for ``SignInKeep.idle_s`` or
``SignInKeep.max_s`` after the sign-in; then, and on any revocation, it is signed out and wiped (``SignsIn.sign_out``).

``SignInRelay`` carries the two things that must reach a person's browser and back without being stored: the
worker holding the login answers the person's describe and code requests, and hears when the person, an admin,
the org's switch or erasure ended the sign-in (``revoked``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from harness_core.engines.errors import EngineRefusal

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from harness_core.engines.types import EngineLaunch
    from harness_core.interactions.ports import RunBinding
    from harness_core.sandbox import SandboxHandle

# How long a person has to sign in once the link is shown (the sign-in attempt's lifetime).
SIGNIN_WAIT_S = 15 * 60
# A kept sign-in (SIGNIN-5): ends after this long without a turn, and this long after the sign-in whatever happens.
KEEP_IDLE_S = 30 * 60
KEEP_IDLE_BOUNDS_S = (5 * 60, 2 * 60 * 60)
KEEP_MAX_S = 12 * 60 * 60
KEEP_MAX_BOUNDS_S = (60 * 60, 24 * 60 * 60)
# How long a revocation is remembered for a kept sign-in to check against: the longest a sign-in can be kept.
REVOCATION_MEMORY_S = KEEP_MAX_BOUNDS_S[1]
# Plans the vendor reports for workspaces an organization administers; any other plan is a consumer plan.
WORK_PLANS = frozenset({"team", "enterprise"})

SignInReason = Literal[
    "signin_unavailable",
    "signin_url_refused",
    "signin_code_invalid",
    "signin_failed",
    "signin_timeout",
    "signin_cancelled",
    "plan_not_allowed",
    "account_disconnected",
]


class SignInRefused(EngineRefusal):
    """The run's own sign-in cannot go on; ``reason_code`` is a ``SignInReason``."""

    reason_code = "signin_failed"


@dataclass(frozen=True, slots=True)
class PlanSignIn:
    """What a run pinned on a person's own plan (route ``vendor_sandbox_login``) carries to its controller."""

    user_id: str
    account_id: str
    # The configured Claude model Node resolved for the plan (ADR-MA-01, the org's model groups); ``None`` on a pin
    # from before it named one, where Claude Code's own default applies.
    model: str | None = None


@dataclass(frozen=True, slots=True)
class SignInHosts:
    """The signed host list of a sandbox-login row: where the sandbox may tunnel, and the hosts a sign-in link
    may point at (the person's browser opens it, never the sandbox)."""

    sandbox: tuple[str, ...]
    link: frozenset[str]


@dataclass(frozen=True, slots=True)
class SignInKeep:
    """How long a signed-in sandbox is kept between a conversation's turns; bounded, the operator picks within."""

    idle_s: int = KEEP_IDLE_S
    max_s: int = KEEP_MAX_S

    def __post_init__(self) -> None:
        if not KEEP_IDLE_BOUNDS_S[0] <= self.idle_s <= KEEP_IDLE_BOUNDS_S[1]:
            raise ValueError(f"the idle timeout must be {KEEP_IDLE_BOUNDS_S[0]}..{KEEP_IDLE_BOUNDS_S[1]} s")
        if not KEEP_MAX_BOUNDS_S[0] <= self.max_s <= KEEP_MAX_BOUNDS_S[1]:
            raise ValueError(f"the maximum lifetime must be {KEEP_MAX_BOUNDS_S[0]}..{KEEP_MAX_BOUNDS_S[1]} s")


@dataclass(frozen=True, slots=True)
class KeptSignIn:
    """A signed-in sandbox parked between turns, as the relay holds it (ids and times only, never the account).

    Written only when a turn ended cleanly and taken (deleted) when the next turn starts, so a turn whose worker died
    leaves none: its sandbox is then signed out and wiped, and the person signs in again."""

    sandbox_id: str
    owner_id: str
    signed_in_at: float
    fence: int

    def encode(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @staticmethod
    def decode(raw: str) -> KeptSignIn | None:
        try:
            doc: object = json.loads(raw)
            if not isinstance(doc, dict):
                return None
            kept = KeptSignIn(**doc)  # pyright: ignore[reportUnknownArgumentType]
        except (ValueError, TypeError):
            return None
        ok = (
            isinstance(kept.sandbox_id, str)
            and isinstance(kept.owner_id, str)
            and isinstance(kept.signed_in_at, (int, float))
            and isinstance(kept.fence, int)
        )
        return kept if ok else None


@dataclass(frozen=True, slots=True)
class SignedIn:
    """What the engine's own status reports after sign-in; never a token. The email goes to Node only."""

    email: str
    plan: str


@dataclass(frozen=True, slots=True)
class CodeRequest:
    """A person's pasted code, and whether their org admits consumer plans (Node decides it per request)."""

    code: str
    work_plans_only: bool


@dataclass(frozen=True, slots=True)
class SignInReply:
    state: Literal["signed_in", "failed"]
    reason_code: str | None = None
    signed_in: SignedIn | None = None
    # A failed code: the attempt stays open with a new link (the engine restarted its login).
    retry: bool = False


class SignInAttempt(Protocol):
    """One open sign-in: the link the worker shows, the codes it is sent."""

    @property
    def attempt_id(self) -> str: ...

    def show(self, link: str) -> None:
        """The link a describe request answers with from now on (a retried login prints a new one)."""
        ...

    async def code(self, timeout_s: float) -> CodeRequest:
        """The next code the owner sent; ``SignInRefused`` (``signin_cancelled``, ``signin_timeout``) otherwise."""
        ...

    async def reply(self, reply: SignInReply) -> None:
        """Answers the code request being handled."""
        ...


class SignInRelay(Protocol):
    def attempt(
        self, run: RunBinding, owner_id: str, attempt_id: str
    ) -> AbstractAsyncContextManager[SignInAttempt]:
        """An attempt the run's owner (only) may describe, send codes to and cancel while the context is open."""
        ...

    def revoked(
        self, run: RunBinding, owner_id: str
    ) -> AbstractAsyncContextManager[asyncio.Event]:
        """While open, the event is set once the owner's sign-in is ended for this run: they disconnected, an admin
        signed them out, the org's switch went off, erasure, or the conversation was shared."""
        ...

    async def keep(self, key: str, kept: KeptSignIn, ttl_s: int) -> None:
        """Parks ``kept`` under ``key`` for ``ttl_s``, replacing whatever was there."""
        ...

    async def take(self, key: str, *, only: KeptSignIn | None = None) -> KeptSignIn | None:
        """Removes and returns what ``key`` holds; with ``only``, just when it is still exactly that record."""
        ...

    async def ended_since(self, run: RunBinding, owner_id: str, at: float) -> bool:
        """Whether a revocation for the owner, the run's session or its org came at or after ``at`` (epoch s)."""
        ...


@runtime_checkable
class SignsIn(Protocol):
    """An adapter whose engine has its own login a person can complete inside the sandbox."""

    def own_login(self, launch: EngineLaunch, model: str | None = None) -> EngineLaunch:
        """The launch without any PipesHub model credential (any would outrank the person's login), running ``model``
        when the pin names one."""
        ...

    async def sign_in(
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        hosts: SignInHosts,
        prompt: Callable[[], AbstractAsyncContextManager[SignInAttempt]],
        progress: Callable[[str, str | None, str | None], Awaitable[None]],
    ) -> SignedIn:
        """Signs the sandbox in, or finds it signed in; raises ``SignInRefused``."""
        ...

    async def sign_out(self, sandbox: SandboxHandle, launch: EngineLaunch) -> None:
        """Signs out at the vendor (best effort) and wipes the engine's config folder; never raises."""
        ...

    async def signed_in(self, sandbox: SandboxHandle, launch: EngineLaunch) -> SignedIn | None:
        """The account the sandbox is signed in to now, without asking the person; ``None`` when there is none."""
        ...

    async def processes(self, sandbox: SandboxHandle) -> frozenset[int]:
        """The processes in the sandbox before a turn starts any: what ``park`` spares (its own keep-alive)."""
        ...

    async def park(self, sandbox: SandboxHandle, spare: frozenset[int]) -> bool:
        """Ends every process but ``spare`` before the sandbox is kept for the next turn; False if it could not."""
        ...


def plan_allowed(plan: str, *, work_plans_only: bool) -> bool:
    return plan in WORK_PLANS or not work_plans_only


__all__ = [
    "KEEP_IDLE_BOUNDS_S",
    "KEEP_IDLE_S",
    "KEEP_MAX_BOUNDS_S",
    "KEEP_MAX_S",
    "REVOCATION_MEMORY_S",
    "SIGNIN_WAIT_S",
    "WORK_PLANS",
    "CodeRequest",
    "KeptSignIn",
    "PlanSignIn",
    "SignInAttempt",
    "SignInHosts",
    "SignInKeep",
    "SignInReason",
    "SignInRefused",
    "SignInRelay",
    "SignInReply",
    "SignedIn",
    "SignsIn",
    "plan_allowed",
]
