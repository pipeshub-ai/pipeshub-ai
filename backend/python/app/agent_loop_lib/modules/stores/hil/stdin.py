from __future__ import annotations

import asyncio
import sys

from app.agent_loop_lib.modules.stores.hil.base import HILRequest, HILResponse, HILStore

_BOLD  = lambda t: f"\033[1m{t}\033[0m"
_CYAN  = lambda t: f"\033[36m{t}\033[0m"
_DIM   = lambda t: f"\033[2m{t}\033[0m"
_YELLOW = lambda t: f"\033[33m{t}\033[0m"


class StdinHILStore(HILStore):
    """HIL store for interactive CLI use.

    When the agent calls clarify(), this store prints the question
    directly to stdout and reads the answer from stdin — no external
    coordinator required.
    """

    def __init__(self) -> None:
        self._requests: dict[str, HILRequest] = {}
        self._responses: dict[str, HILResponse] = {}

    async def submit(self, request: HILRequest) -> str:
        self._requests[request.request_id] = request
        return request.request_id

    async def get_request(self, request_id: str) -> HILRequest | None:
        return self._requests.get(request_id)

    async def respond(self, response: HILResponse) -> None:
        self._responses[response.request_id] = response

    async def wait_for_response(
        self, request_id: str, timeout: float | None = None
    ) -> HILResponse:
        if request_id not in self._requests:
            raise KeyError(f"Unknown HIL request: {request_id!r}")

        # Already answered (e.g. by a prior respond() call)
        if request_id in self._responses:
            return self._responses[request_id]

        request = self._requests[request_id]
        question = request.question or "(no question provided)"

        # Print the clarification question in a visually distinct block
        print()
        print(_BOLD(_YELLOW("  ❓ Clarification needed:")))
        print(f"  {_CYAN(question)}")
        print(_DIM("  (Press Enter with no input to skip / type your answer and press Enter)"))
        print()

        loop = asyncio.get_event_loop()
        if sys.stdin.isatty():
            sys.stdout.write("  Your answer: ")
            sys.stdout.flush()

        try:
            raw = await asyncio.wait_for(
                loop.run_in_executor(None, sys.stdin.readline),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raw = ""

        answer = raw.strip()
        approved = bool(answer)

        response = HILResponse(
            request_id=request_id,
            approved=approved,
            answer=answer or None,
        )
        self._responses[request_id] = response
        return response

    async def get_response(self, request_id: str) -> HILResponse | None:
        return self._responses.get(request_id)

    async def list_pending(self, session_id: str | None = None) -> list[HILRequest]:
        answered = set(self._responses.keys())
        pending = [r for r in self._requests.values() if r.request_id not in answered]
        if session_id is not None:
            pending = [r for r in pending if r.session_id == session_id]
        return pending
