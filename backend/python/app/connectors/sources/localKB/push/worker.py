"""The CPU stage of a push, in a worker process.

``validate_body`` is the whole of task 1: the structural pass, the strict parse,
the image and structure checks, ``BlockContainerValidator``, the property
values, and the canonical stored bytes. It is a plain function so tests call it
directly; ``PushWorker`` runs it in one long-lived spawned process, leased by one
push at a time, so a parse never stalls the event loop and a runaway body costs
one process, not the service.

The worker reads the body from, and writes the stored bytes to, files the parent
owns (``spool.py``). A death of the worker during the task is answered by the
parent: SIGABRT, SIGSEGV and SIGBUS (pydantic-core aborts on an allocation
failure) are 413 ``complexity``; anything else is 503.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import multiprocessing
import re
import signal
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .contract import PushRefusal

if TYPE_CHECKING:
    from multiprocessing.connection import Connection

TASK_BUDGET_S = 45.0
KILL_AFTER_S = 50.0
LEASE_WAIT_S = 10.0
MAX_TASKS_PER_WORKER = 32
ADDRESS_SPACE_HEADROOM = 1 << 30

_SCHEMA_ID_HINT = re.compile(rb'"schemaId"\s*:\s*"([^"\\]{1,300})"')
_DEATH_SIGNALS = frozenset({signal.SIGABRT, signal.SIGSEGV, signal.SIGBUS})

logger = logging.getLogger(__name__)


def schema_id_hint(body: bytes) -> str | None:
    """The first ``"schemaId": "…"`` in the body, to pick the schema whose property names the
    structural pass allows. It is only a hint: the parsed ``metadata.schemaId`` must equal it."""
    m = _SCHEMA_ID_HINT.search(body)
    return m.group(1).decode("utf-8", "replace") if m else None


@dataclass
class Validated:
    record_name: str
    external_record_id: str
    version: int
    source_last_modified: int | None
    schema_id: str | None
    properties: dict[str, Any] | None
    blocks_sha: str
    digest: str
    stored_path: str
    size: int
    content_rev: str
    warnings: int
    block_count: int
    group_count: int


def _refusal_dict(r: PushRefusal) -> dict[str, Any]:
    return {"status": r.status, "reason": r.reason, "message": r.message, "location": r.location,
            "issues": r.issues, "extra": r.extra}


def refusal_from_dict(d: dict[str, Any]) -> PushRefusal:
    return PushRefusal(d["status"], d["reason"], d["message"], d.get("location"), d.get("issues"), d.get("extra"))


def validate_body(body: bytes, *, schema_id: str | None, schema: dict[str, Any] | None, out_path: str) -> Validated:
    """Everything that can be decided from the body and the KB's schema. Raises ``PushRefusal``."""
    from app.exceptions.indexing_exceptions import BlockContainerValidationError
    from app.models.record_metadata import (
        MetadataSchema,
        MetadataValidationError,
        validate_values,
    )
    from app.modules.transformers.block_container_validator import (
        BlockContainerValidator,
        Severity,
    )

    from . import contract as C
    from .images import check_blocks_images
    from .structure import structure_pass

    schema_obj = MetadataSchema.model_validate(schema) if schema is not None else None
    names = frozenset(p.name for p in schema_obj.properties) if schema_obj else frozenset()
    structure_pass(body, names)
    env = C.parse_envelope(body)

    properties: dict[str, Any] | None = None
    used_schema: str | None = None
    if env.metadata is not None:
        used_schema = env.metadata.schemaId
        if schema_obj is None or used_schema != schema_id or used_schema != schema_obj.schema_id:
            raise PushRefusal(422, "SCHEMA_MISMATCH", "metadata.schemaId does not name a schema of this knowledge base.",
                              "metadata.schemaId", extra={"code": "ENVELOPE_INVALID"})
        try:
            checked = validate_values(schema_obj, dict(env.metadata.values), source="push")
        except MetadataValidationError as e:
            raise PushRefusal(422, e.code, str(e), e.property, extra={"code": "ENVELOPE_INVALID", "property": e.property}) from None
        properties = C.canonical_properties(used_schema, {k: v.value for k, v in checked.values.items()})

    check_blocks_images(env.blocks)
    try:
        warnings = BlockContainerValidator().validate(env.blocks)
    except BlockContainerValidationError as e:
        issues = [{"location": i.location, "type": i.code, "message": i.message} for i in e.errors[:C.MAX_ISSUES]]
        raise PushRefusal(422, "BLOCKS_INVALID", "The blocks are invalid.", issues[0]["location"], issues, {"code": "BLOCKS_INVALID"}) from None

    canon = C.canonical_blocks(env.blocks)
    blocks_sha = C.blocks_digest(canon)
    stored = C.stored_bytes(env.blocks, properties)
    with open(out_path, "wb") as f:
        f.write(stored)
    return Validated(
        record_name=env.recordName,
        external_record_id=env.externalRecordId,
        version=env.version,
        source_last_modified=env.sourceLastModifiedTimestamp,
        schema_id=used_schema,
        properties=properties,
        blocks_sha=blocks_sha,
        digest=C.request_digest(blocks_sha, properties),
        stored_path=out_path,
        size=len(stored),
        content_rev=C.content_rev(stored),
        warnings=len([w for w in warnings if w.severity == Severity.WARNING]),
        block_count=len(env.blocks.blocks or []),
        group_count=len(env.blocks.block_groups or []),
    )


# ---- the worker process ---------------------------------------------------------------------

def _vm_size() -> int:
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmSize:"):
                return int(line.split()[1]) * 1024
    return 0


def _worker_main(conn: Connection) -> None:
    import importlib
    import resource

    # Imported before the address-space limit is set, so the limit measures task memory.
    for module in ("app.modules.transformers.block_container_validator", f"{__package__}.contract",
                   f"{__package__}.images", f"{__package__}.structure"):
        importlib.import_module(module)

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    size = _vm_size()
    if size:
        limit = size + ADDRESS_SPACE_HEADROOM
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    conn.send(("ready", None))
    while True:
        try:
            task = conn.recv()
        except EOFError:
            return
        if task is None:
            return
        in_path, out_path, schema_id, schema = task
        body = None
        try:
            with open(in_path, "rb") as f:
                body = f.read()
            result = validate_body(body, schema_id=schema_id, schema=schema, out_path=out_path)
            conn.send(("ok", result))
        except PushRefusal as r:
            conn.send(("refused", _refusal_dict(r)))
        except MemoryError:
            conn.send(("refused", _refusal_dict(PushRefusal(413, "PAYLOAD_TOO_LARGE", "The body is too complex to parse.", extra={"limit": "complexity"}))))
        except Exception as e:  # a bug must not kill the leased worker silently
            conn.send(("error", f"{type(e).__name__}: {e}"[:300]))
        finally:
            body = None  # free the body before waiting for the next task


class PushWorker:
    """One worker process, leased by one push at a time."""

    def __init__(self, *, lease_wait_s: float = LEASE_WAIT_S, kill_after_s: float = KILL_AFTER_S) -> None:
        self._lease = asyncio.Lock()
        self._proc: multiprocessing.process.BaseProcess | None = None
        self._conn: Any = None
        self._tasks = 0
        self.lease_wait_s = lease_wait_s
        self.kill_after_s = kill_after_s
        self.deaths = 0

    async def _ensure(self) -> None:
        if self._proc is not None and self._proc.is_alive() and self._tasks < MAX_TASKS_PER_WORKER:
            return
        await self._stop()
        await asyncio.to_thread(self._start)
        self._tasks = 0

    def _start(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe(duplex=True)
        proc = ctx.Process(target=_worker_main, args=(child,), daemon=True, name="kb-push-worker")
        proc.start()
        child.close()
        if not parent.poll(60):
            proc.kill()
            raise RuntimeError("the push worker did not start")
        kind, _ = parent.recv()
        assert kind == "ready"
        self._proc, self._conn = proc, parent

    async def _stop(self) -> None:
        proc, conn = self._proc, self._conn
        self._proc = self._conn = None
        if proc is None:
            return

        def stop() -> None:
            with contextlib.suppress(Exception):
                if proc.is_alive():
                    proc.kill()
                proc.join(5)
            with contextlib.suppress(Exception):
                conn.close()

        await asyncio.to_thread(stop)

    async def validate(self, in_path: str, out_path: str, *, schema_id: str | None, schema: dict[str, Any] | None) -> Validated:
        try:
            await asyncio.wait_for(self._lease.acquire(), self.lease_wait_s)
        except TimeoutError:
            raise PushRefusal(503, "PUSH_BUSY", "The server is busy with other pushes.", extra={"retryAfter": 2}) from None
        sent = received = False
        try:
            await self._ensure()
            self._tasks += 1
            conn, proc = self._conn, self._proc
            sent = True
            await asyncio.to_thread(conn.send, (in_path, out_path, schema_id, schema))
            started = time.monotonic()
            ready = await asyncio.to_thread(conn.poll, self.kill_after_s)
            if not ready:
                await self._stop()
                raise PushRefusal(503, "PUSH_TIMEOUT", "The push took too long to validate.", extra={"retryAfter": 5})
            try:
                kind, payload = await asyncio.to_thread(conn.recv)
            except (EOFError, OSError):
                await asyncio.to_thread(proc.join, 5)
                code = proc.exitcode
                await self._stop()
                self.deaths += 1
                if code is not None and code < 0 and signal.Signals(-code) in _DEATH_SIGNALS:
                    raise PushRefusal(413, "PAYLOAD_TOO_LARGE", "The body is too complex to parse.", extra={"limit": "complexity"}) from None
                raise PushRefusal(503, "PUSH_TIMEOUT", "The validation worker stopped.", extra={"retryAfter": 5}) from None
            received = True
            logger.debug("push validated in %.2fs", time.monotonic() - started)
            if kind == "ok":
                return payload
            if kind == "refused":
                raise refusal_from_dict(payload)
            raise RuntimeError(f"push worker failed: {payload}")
        finally:
            try:
                if sent and not received:
                    # Cancelled (or failed) with the task still running: its result would be
                    # the next lease holder's answer. Shielded so a second cancel cannot skip the release.
                    await asyncio.shield(self._stop())
            finally:
                self._lease.release()

    async def close(self) -> None:
        await self._stop()
