"""A circuit breaker per MCP server and credential owner (MX-08), kept by the process's hub.

Each server's own connect and call timeouts bound one request; the breaker stops a server that keeps failing from
costing every later request its full timeout. After ``failures`` failures in a row it opens: calls and listings of
that server fail at once (``MCPServerCircuitOpenError``) while every other server is untouched. After ``cooldown_s``
one request goes through as the probe; its answer closes the circuit, its failure opens it for another cooldown.

Only a timeout or an unreachable server counts (``classify_mcp_failure``). A server that answers, even with an error,
a refused sign-in or a refused argument, is up. A call that keeps reporting progress past the per-call ceiling
is a slow job, not a dead server. Breakers are keyed per credential owner as sessions are, so one user's
misconfigured credential cannot fail a server for another.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, TypeVar

from app.agents.mcp.client import MCPCallTooLongError
from app.agents.mcp.errors import MCPServerCircuitOpenError
from app.agents.mcp.failure import MCPFailureReason, classify_mcp_failure
from app.services.base_client import CircuitBreaker
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.mcp_hub.types import SessionKey

T = TypeVar("T")
_LOG = HarnessLog.named("app.harness.mcp_hub")
# The shared breaker logs its own transitions on a stdlib logger; its name carries the instance id only.
_BREAKER_LOGGER = logging.getLogger("app.harness.mcp_hub.breaker")
_DOWN = frozenset({MCPFailureReason.TIMEOUT, MCPFailureReason.UNREACHABLE})


def server_down(exc: BaseException) -> bool:
    """Whether ``exc`` says the server is down or too slow, rather than that it answered."""
    return not isinstance(exc, MCPCallTooLongError) and classify_mcp_failure(exc) in _DOWN


class ServerBreakers:
    def __init__(self, *, failures: int, cooldown_s: float) -> None:
        self._failures = failures
        self._cooldown_s = cooldown_s
        self._breakers: dict[SessionKey, CircuitBreaker] = {}

    @property
    def enabled(self) -> bool:
        return self._failures > 0

    def _breaker(self, key: SessionKey) -> CircuitBreaker:
        breaker = self._breakers.get(key)
        if breaker is None:
            breaker = CircuitBreaker(
                f"mcp:{key.server_id}",
                failure_threshold=self._failures,
                cooldown_seconds=self._cooldown_s,
                logger=_BREAKER_LOGGER,
            )
            self._breakers[key] = breaker
        return breaker

    def state(self, key: SessionKey) -> str:
        breaker = self._breakers.get(key)
        return breaker.state if breaker is not None else "closed"

    def _admit(self, key: SessionKey, label: str) -> CircuitBreaker:
        breaker = self._breaker(key)
        # Claiming the probe first: once the cooldown has passed exactly one request runs, and the rest still fail fast.
        if breaker.should_attempt_probe():
            return breaker
        if breaker.is_open:
            retry_after = breaker.retry_after()
            _LOG.info("mcp_hub.circuit_refused", orgId=key.tenant, mcpServer=key.server_id, retryAfterS=retry_after)
            raise MCPServerCircuitOpenError(label, retry_after)
        return breaker

    async def run(self, key: SessionKey, label: str, request: Callable[[], Awaitable[T]]) -> T:
        """``request()`` unless the server's circuit is open; its outcome is recorded. ``label`` names the server
        in the refusal the model and the user read."""
        if not self.enabled:
            return await request()
        breaker = self._admit(key, label)
        try:
            result = await request()
        except Exception as exc:
            if server_down(exc):
                before = breaker.state
                breaker.record_failure()
                if before != "open" and breaker.state == "open":
                    _LOG.warning(
                        "mcp_hub.circuit_open", orgId=key.tenant, mcpServer=key.server_id, errorType=type(exc).__name__
                    )
            else:
                breaker.record_success()
            raise
        breaker.record_success()
        return result


__all__ = ["ServerBreakers", "server_down"]
