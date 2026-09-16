"""`RunCancellationRegistry`: the contract `stream_bridge.py`/`bridge.py`
use to make an in-flight agent-loop run cooperatively cancellable, and
`POST /chat/cancel` (`chatbot.py`) uses to request that cancellation.

Two implementations (see `in_process.py`/`kv_backed.py`), chosen by
`factory.py::build_run_cancellation_registry()` and wired as a singleton
on `QueryAppContainer` — callers depend on this Protocol, never on either
concrete class, so a single-worker install and a multi-replica Helm
deployment run the exact same `stream_bridge.py`/`chatbot.py` code.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.agent_loop_lib.core.context import CancellationToken

__all__ = ["CancelOutcome", "RunCancellationRegistry", "RunOwner"]

# "cancelled": the token was set (or, cross-process, the cancel signal was
#   published for the owning worker to pick up).
# "not_found": no active run is registered under this run_id — either it
#   never existed, already completed (register()'s caller always calls
#   unregister() in a finally), or (KV-backed, cross-process publish path)
#   the publish itself failed.
# "forbidden": a run IS active under this run_id, but `requester` doesn't
#   match the `RunOwner` it was registered with.
CancelOutcome = Literal["cancelled", "not_found", "forbidden"]


class RunOwner(BaseModel):
    """Identity a run was registered under — compared against the
    requester's own identity on `cancel()` so one user can never cancel
    another user's (or another org's) in-flight run by guessing/
    enumerating a `runId`, NOR cancel one of their OWN other conversations'
    runs through a cancel request scoped to a different conversation.
    `conversation_id` is only enforced when both the registered owner and
    the requester carry one — see `InProcessRunCancellationRegistry.cancel()`."""

    user_id: str
    org_id: str
    conversation_id: str | None = None


class RunCancellationRegistry(Protocol):
    """Every method is async because the KV-backed implementation talks to
    Redis/etcd on `cancel()` (cross-process publish) and `unregister()`
    (delete the published key) — the in-process implementation satisfies
    the same async signatures trivially."""

    async def is_active(self, run_id: str) -> bool:
        """Whether `run_id` currently has a registered, not-yet-unregistered
        run on THIS worker. Used by the route layer (`chatbot.py`/
        `agent.py`) to reject a duplicate client-supplied `runId` with a
        real HTTP 409 before the `StreamingResponse` starts — once the SSE
        generator is running, the response is already committed to 200."""
        ...

    async def register(self, run_id: str, token: "CancellationToken", owner: RunOwner) -> None:
        """Make `run_id` cancellable. Call BEFORE `build_initial_state()` so
        even the "Thinking" phase (before the first LLM call) is
        cancellable — see `stream_bridge.py`/`bridge.py`."""
        ...

    async def cancel(self, run_id: str, requester: RunOwner) -> CancelOutcome:
        """Request cancellation of `run_id` on behalf of `requester`."""
        ...

    async def unregister(self, run_id: str) -> None:
        """Tear down everything `register()` set up for `run_id` — always
        called from the producer's `finally` block, whether the run
        completed normally, failed, or was cancelled."""
        ...
