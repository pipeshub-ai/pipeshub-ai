from __future__ import annotations

import asyncio

from app.agent_loop_lib.modules.stores.hil.base import (
    HILRequest,
    HILResponse,
    HILStore,
)


class InMemoryHILStore(HILStore):
    def __init__(self) -> None:
        self._requests: dict[str, HILRequest] = {}
        self._responses: dict[str, HILResponse] = {}
        self._events: dict[str, asyncio.Event] = {}

    async def submit(self, request: HILRequest) -> str:
        self._requests[request.request_id] = request
        self._events[request.request_id] = asyncio.Event()
        return request.request_id

    async def get_request(self, request_id: str) -> HILRequest | None:
        return self._requests.get(request_id)

    async def respond(self, response: HILResponse) -> None:
        if response.request_id not in self._requests:
            raise KeyError(f"Unknown HIL request: {response.request_id!r}")
        self._responses[response.request_id] = response
        event = self._events.get(response.request_id)
        if event is not None:
            event.set()

    async def wait_for_response(
        self, request_id: str, timeout: float | None = None
    ) -> HILResponse:
        if request_id not in self._requests:
            raise KeyError(f"Unknown HIL request: {request_id!r}")
        # Already answered?
        if request_id in self._responses:
            return self._responses[request_id]
        event = self._events[request_id]
        await asyncio.wait_for(event.wait(), timeout=timeout)
        return self._responses[request_id]

    async def get_response(self, request_id: str) -> HILResponse | None:
        return self._responses.get(request_id)

    async def list_pending(self, session_id: str | None = None) -> list[HILRequest]:
        answered = set(self._responses.keys())
        pending = [r for r in self._requests.values() if r.request_id not in answered]
        if session_id is not None:
            pending = [r for r in pending if r.session_id == session_id]
        return pending
