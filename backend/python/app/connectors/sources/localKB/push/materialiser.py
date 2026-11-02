"""The image materialiser: a pool of two spawned processes that fetch ``storage://`` images and splice them in.

The parent (this module) holds the policy; the child (``materialiser_child.py``) holds the user's data and a
storage token and imports nothing from the application. A task is one push: the sentinel file the worker wrote,
the references with what the verified ``info`` said about each, the effective cap and a short-lived token.
The child writes the inlined file and answers its size and ``contentRev``; a death, an overrun or a refusal is
translated here. A killed child is replaced, so a push cannot leave a poisoned process behind.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import os
import shutil
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import materialiser_child as child
from .contract import PushRefusal

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

POOL_SIZE = 2
WALL_BUDGET_S = 90.0
START_TIMEOUT_S = 60.0
TOKEN_TTL_S = 180

#: ``outcome`` values of ``on_task_end``: the 8b-4 ledger plugs in here; until then it is a no-op.
OUTCOMES = ("ok", "refused", "overrun", "died")


@dataclass
class Materialised:
    size: int
    content_rev: str


def _refusal(d: dict[str, Any]) -> PushRefusal:
    return PushRefusal(d["status"], d["reason"], d["message"], extra=d.get("extra") or {})


class _Slot:
    def __init__(self) -> None:
        self.proc: multiprocessing.process.BaseProcess | None = None
        self.conn: Any = None
        self.busy = False


class Materialiser:
    """``run`` gives one task to a free child and waits (at most ``wall_budget_s``) for its answer.

    The hooks are constructor arguments and ``None``/no-op in production: ``resolver_override`` and ``ca_file``
    let a test reach a local server under a real host name, ``allow_loopback`` admits a loopback stored URL,
    ``before_task`` runs in the parent just before a task is handed to the child (a race test replaces an image
    there), ``on_task_end`` receives ``(outcome, cpu_s)``.
    """

    def __init__(
        self,
        *,
        pool_size: int = POOL_SIZE,
        wall_budget_s: float = WALL_BUDGET_S,
        listed_origins: list[str] | None = None,
        resolver_override: dict[str, str] | None = None,
        ca_file: str | None = None,
        allow_loopback: bool = False,
        before_task: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        on_task_end: Callable[[str, float], None] | None = None,
    ) -> None:
        child.validate_override(resolver_override)
        self.listed_origins = child.parse_origin_list(listed_origins or [], allow_loopback_http=allow_loopback)
        self.resolver_override, self.ca_file, self.allow_loopback = resolver_override, ca_file, allow_loopback
        self.wall_budget_s = wall_budget_s
        self.before_task = before_task
        self.on_task_end = on_task_end or (lambda outcome, cpu_s: None)
        self._slots = [_Slot() for _ in range(pool_size)]
        self._free: asyncio.Semaphore | None = None
        self._pool_size = pool_size
        self.spawned = 0

    # ---- processes ------------------------------------------------------------------------------

    def _start(self, slot: _Slot) -> None:
        ctx = multiprocessing.get_context("spawn")
        parent, remote = ctx.Pipe(duplex=True)
        proc = ctx.Process(target=child.child_main, args=(remote,), daemon=True, name="kb-push-materialiser")
        proc.start()
        remote.close()
        if not parent.poll(START_TIMEOUT_S):
            proc.kill()
            raise RuntimeError("the push materialiser did not start")
        parent.recv()
        slot.proc, slot.conn = proc, parent
        self.spawned += 1

    @staticmethod
    def _kill(slot: _Slot) -> multiprocessing.process.BaseProcess | None:
        """Forget the slot's child and SIGKILL it; never waits. Returns the process so the caller can reap it off the loop."""
        proc, conn = slot.proc, slot.conn
        slot.proc = slot.conn = None
        with contextlib.suppress(Exception):
            if proc is not None and proc.is_alive():
                proc.kill()
        with contextlib.suppress(Exception):
            if conn is not None:
                conn.close()
        return proc

    @staticmethod
    async def _reap(proc: multiprocessing.process.BaseProcess | None) -> None:
        """Wait for a killed child in a thread: a process in uninterruptible I/O can take seconds, and the loop is shared."""
        if proc is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(proc.join, 5)

    async def _acquire(self) -> _Slot:
        if self._free is None:
            self._free = asyncio.Semaphore(self._pool_size)
        await self._free.acquire()
        slot = next(s for s in self._slots if not s.busy)
        slot.busy = True
        return slot

    def _release(self, slot: _Slot) -> None:
        slot.busy = False
        assert self._free is not None
        self._free.release()

    # ---- a task ---------------------------------------------------------------------------------

    def make_dir(self, spool_dir: str) -> str:
        """The per-push directory every temp file of a task lives in, owned (and removed) by the parent."""
        path = os.path.join(spool_dir, f"img-{os.urandom(8).hex()}")
        os.makedirs(path, mode=0o700)
        return path

    async def run(self, task: dict[str, Any]) -> Materialised:
        """Run ``task`` (see ``materialiser_child.run_task``). Raises ``PushRefusal``."""
        task = {
            **task, "listed_origins": self.listed_origins, "resolver_override": self.resolver_override,
            "ca_file": self.ca_file, "allow_loopback": self.allow_loopback,
        }
        slot = await self._acquire()
        started = time.monotonic()
        outcome = "died"
        try:
            if slot.proc is None or not slot.proc.is_alive():
                await self._reap(self._kill(slot))
                await asyncio.to_thread(self._start, slot)
            if self.before_task is not None:
                await self.before_task(task)
            conn, proc = slot.conn, slot.proc
            try:
                await asyncio.to_thread(conn.send, task)
            except OSError:  # the child is gone (killed between the check and the send)
                await self._reap(self._kill(slot))
                raise PushRefusal(503, "PUSH_TIMEOUT", "The image worker stopped; retry.", extra={"retryAfter": 5}) from None
            if not await asyncio.to_thread(conn.poll, self.wall_budget_s):
                outcome = "overrun"
                await self._reap(self._kill(slot))
                raise PushRefusal(503, "PUSH_TIMEOUT", "The images took too long to process.", extra={"retryAfter": 5})
            try:
                kind, payload = await asyncio.to_thread(conn.recv)
            except (EOFError, OSError):
                await asyncio.to_thread(proc.join, 5)
                code = proc.exitcode
                await self._reap(self._kill(slot))
                logger.warning("the push materialiser died (exit %s)", code)
                raise PushRefusal(503, "PUSH_TIMEOUT", "The image worker stopped; retry.", extra={"retryAfter": 5}) from None
            if kind == "ok":
                outcome = "ok"
                return Materialised(payload["size"], payload["content_rev"])
            outcome = "refused"
            if kind == "refused":
                logger.warning("the push materialiser refused a task: %s %s (%s)", payload.get("status"), payload.get("reason"), payload.get("message"))
                raise _refusal(payload)
            await self._reap(self._kill(slot))  # an unexpected error: do not trust the process again
            raise PushRefusal(503, "PUSH_TIMEOUT", "The images could not be processed; retry.", extra={"retryAfter": 5})
        except asyncio.CancelledError:
            # The task may still be running; its answer would be the next push's. The kill is immediate; only the reap waits.
            await asyncio.shield(self._reap(self._kill(slot)))
            raise
        finally:
            self._release(slot)
            self.on_task_end(outcome, time.monotonic() - started)

    @staticmethod
    def remove_dir(path: str | None) -> None:
        if path:
            shutil.rmtree(path, ignore_errors=True)

    async def close(self) -> None:
        for slot in self._slots:
            await self._reap(self._kill(slot))
