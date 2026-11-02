"""Per-request phase timing for the query service (load-test instrumentation).

Dropped into the running container at /app/python/app/utils/query_phase_timing.py
by loadtest/patch_instrumentation.sh. NOT part of the repo's shipped code and
must never be committed under backend/.

The _Phases object is held in a contextvar rather than threaded through
run_chat_stream()'s signature, so app/agents/chat_modes/bridge.py can mark
phases without changing a function signature shared by other call sites.

Context propagation this relies on:
  - async generators run in the context of whoever drives them, so a set() in
    chatbot.py's stream generator is visible to bridge.py's run_chat_stream;
  - asyncio.create_task() copies the current context, so bridge.py's _produce()
    task sees the same _Phases instance and mutates it by reference.
Each request is a separate task, so there is no cross-request bleed.

Enabled unless QUERY_PHASE_TIMING is set to a falsey value: the module only
exists on a patched container, so its presence is already the opt-in.
"""

import contextvars
import os
import time
import uuid

_ENABLED = os.getenv("QUERY_PHASE_TIMING", "1").strip().lower() not in (
    "0",
    "false",
    "no",
    "off",
)

_current: contextvars.ContextVar = contextvars.ContextVar(
    "query_phase_timing", default=None
)


class _Phases:
    __slots__ = ("logger", "qid", "t0", "last")

    def __init__(self, logger, qid: str) -> None:
        self.logger = logger
        self.qid = qid
        self.t0 = self.last = time.monotonic()

    def mark(self, name: str) -> None:
        now = time.monotonic()
        try:
            self.logger.info(
                "PHASE qid=%s step=%s ms=%.0f cum=%.0f",
                self.qid,
                name,
                (now - self.last) * 1000.0,
                (now - self.t0) * 1000.0,
            )
        except Exception:
            pass
        self.last = now


def phase_start(logger):
    """Begin timing this request and bind it to the current context."""
    if not _ENABLED:
        return None
    ph = _Phases(logger, uuid.uuid4().hex[:8])
    _current.set(ph)
    return ph


def phase_mark(name: str) -> None:
    """Mark a phase against whatever _Phases the current context holds."""
    if not _ENABLED:
        return
    ph = _current.get()
    if ph is not None:
        ph.mark(name)
