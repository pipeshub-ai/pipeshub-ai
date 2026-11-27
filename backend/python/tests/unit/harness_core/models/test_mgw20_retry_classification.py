"""MGW-20: one failure classifies the same on every transport.

The retry hook reads ``retryable`` and ``status_code`` off the ``TransportError``, and the fallback policy reads the
trigger off it and its causes; a provider whose transport disagrees retries what another gives up on, or never falls
back. Each fault is driven through the real request path at the socket, for a plain and a streamed call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
import pytest

from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import Message, UserMessage
from harness_core.models.policy import FallbackTrigger, classify_failure
from tests.unit.harness_core.models.transport_wire import RIGS, Rig

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.models.transports.base import LLMTransport

_MESSAGES: list[Message] = [UserMessage(content="hello")]


@dataclass(frozen=True)
class Fault:
    name: str
    answer: Callable[[Rig, httpx.Request], httpx.Response]
    retryable: bool
    status_code: int | None
    trigger: FallbackTrigger | None


def _raises(exc: Exception) -> Callable[[Rig, httpx.Request], httpx.Response]:
    def answer(_rig: Rig, _request: httpx.Request) -> httpx.Response:
        raise exc

    return answer


def _status(status: int) -> Callable[[Rig, httpx.Request], httpx.Response]:
    return lambda rig, _request: rig.error(status)


_FAULTS: tuple[Fault, ...] = (
    # WP-100 (G-48): a provider that cannot be reached is down, as a 5xx says: retried on its route, then moved.
    Fault("connect_refused", _raises(httpx.ConnectError("connection refused")), True, None, "5xx"),
    Fault("connection_dropped", _raises(httpx.RemoteProtocolError("server disconnected")), True, None, "5xx"),
    Fault("os_error", _raises(ConnectionResetError(104, "connection reset by peer")), True, None, "5xx"),
    Fault("read_timeout", _raises(httpx.ReadTimeout("read timed out")), True, None, "timeout"),
    Fault("status_429", _status(429), True, 429, None),
    Fault("status_500", _status(500), True, 500, "5xx"),
    Fault("status_502", _status(502), True, 502, "5xx"),
    Fault("status_503", _status(503), True, 503, "5xx"),
    Fault("status_504", _status(504), True, 504, "5xx"),
    Fault("status_529", _status(529), True, 529, "5xx"),
    Fault("status_400", _status(400), False, 400, None),
    Fault("status_401", _status(401), False, 401, None),
    Fault("status_404", _status(404), False, 404, None),
)


async def _complete(transport: LLMTransport) -> None:
    await transport.complete(_MESSAGES)


async def _stream(transport: LLMTransport) -> None:
    async for _ in transport.stream(_MESSAGES):
        pass


@pytest.mark.parametrize("call", [_complete, _stream], ids=["complete", "stream"])
@pytest.mark.parametrize("rig", RIGS, ids=[r.name for r in RIGS])
@pytest.mark.parametrize("fault", _FAULTS, ids=[f.name for f in _FAULTS])
async def test_mgw20_a_fault_classifies_the_same_on_every_transport(
    fault: Fault, rig: Rig, call: Callable[[LLMTransport], Awaitable[None]]
) -> None:
    transport = rig.build(lambda request: fault.answer(rig, request))

    with pytest.raises(TransportError) as raised:
        await call(transport)

    error = raised.value
    assert (error.retryable, error.status_code, classify_failure(error)) == (
        fault.retryable,
        fault.status_code,
        fault.trigger,
    )
