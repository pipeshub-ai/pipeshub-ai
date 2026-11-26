"""Bulk knowledge export into the run's sandbox, asynchronously (G-35, GA-19).

Analysis needs thousands of records, and paging a search through the model is slow and fills its context. An export
reads them in the background, as the person the run acts for (each page is the knowledge service's own ACL-filtered
listing), and writes them as JSONL on the run's knowledge shelf (the host's folder in the sandbox), where the engine reads
them with its shell. Two tools, in the shape of MCP Tasks (which today's engine clients do not speak): the start tool
answers at once with a task id, and the status tool waits a bounded while for the task and says where its file is.

Accounting: the start call's outcome journals ``started`` with the records the export may write (its limit after the
host's ``allowance``); the status call that first reports the end journals ``finished`` with what it wrote, and gives
back the rest. An export whose end is never reported keeps what ``started`` named: an attempt that dies counts what it
might have written, never less.

Product-neutral: the record source, the shelf, the events and the tool names are the host's.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, Protocol, cast

from harness_core.contracts import canonical_sha256
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.types import ToolEffect, ToolExecution, tool_id_for
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import ToolContext
    from harness_core.invoker.ports import ToolCatalog
    from harness_core.journal.types import NewEvent
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.workspace.sync.storage_classes import Reference

# Rows one page of the source is asked for.
EXPORT_PAGE: Final = 200
# The most one export's file holds; an export past it stops there (``limited``).
MAX_EXPORT_BYTES: Final = 48 * 1024 * 1024
# How long one status call waits for its task; the engine's MCP timeout is at least 150 s.
MAX_WAIT_S: Final = 60
DEFAULT_WAIT_S: Final = 20
ExportStatus = Literal["working", "completed", "limited", "failed", "cancelled"]
_DONE: Final = frozenset({"completed", "limited", "failed", "cancelled"})
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass(frozen=True, slots=True)
class RecordPage:
    rows: tuple[Mapping[str, JsonValue], ...]
    # Where the next page starts; ``None``: this was the last.
    next: str | None = None


class RecordPages(Protocol):
    """One export's records as the person may read them, a page at a time."""

    async def __call__(self, cursor: str | None, limit: int) -> RecordPage: ...


class ExportShelf(Protocol):
    """Writes a file on the run's knowledge shelf: its path in the sandbox, or ``None`` while no sandbox holds the
    shelf (between turns) or when it could not be written."""

    async def write(self, name: str, text: str) -> str | None: ...


class ShelvesFiles(Protocol):
    """A turn's tool endpoint while its sandbox holds the shelf (the engine controller's)."""

    async def shelve(self, name: str, text: str, *, suffix: str = ".md") -> str | None: ...

    async def hand_off(self, archive: AsyncIterator[bytes], size_bytes: int) -> str | None:
        """Seeds ``archive`` (a complete tar of ``size_bytes``, paths relative to the turn's read-only repository
        folder) into its sandbox; the folder's path there, or ``None`` while no sandbox holds it (WP-63 part A)."""
        ...


class ShelfRelay:
    """``ExportShelf`` over whichever turn of the run holds the shelf now: the engine controller attaches each turn's
    endpoint once its sandbox has the tool folder, and detaches it before the sandbox is released."""

    def __init__(self) -> None:
        self._target: ShelvesFiles | None = None

    def attach(self, target: ShelvesFiles) -> None:
        self._target = target

    def detach(self, target: ShelvesFiles) -> None:
        if self._target is target:
            self._target = None

    async def write(self, name: str, text: str) -> str | None:
        target = self._target
        return None if target is None else await target.shelve(name, text, suffix=".jsonl")

    async def hand_off(self, archive: AsyncIterator[bytes], size_bytes: int) -> str | None:
        """``archive`` into the sandbox of the turn that holds the shelf now; ``None`` between turns."""
        target = self._target
        return None if target is None else await target.hand_off(archive, size_bytes)

    def reference_for(self, address: str) -> Reference | None:
        """The session's recorded reference of the repository at ``address`` (a ``Reference``), from the turn that
        holds the shelf now; ``None`` between turns or when no earlier turn fetched it (ADR-RUN-01 D2)."""
        found = getattr(self._target, "reference_for", None)
        return None if found is None else cast("Reference | None", found(address))

    def fetched(self, address: str) -> None:
        """The worker fetched the repository at ``address`` into the turn that holds the shelf now."""
        note = getattr(self._target, "fetched", None)
        if note is not None:
            note(address)

    def sandbox_id(self) -> str | None:
        """The sandbox of the turn that holds the shelf now; ``None`` between turns."""
        target = self._target
        sandbox_id = getattr(target, "sandbox_id", None)
        return None if sandbox_id is None else sandbox_id()

    async def hold_flow(self, *, run_id: str, hold: str) -> bool | None:
        """A flow hold on the sandbox of the turn that holds the shelf now, through its broker (R38EGRESS-FU-1);
        ``None`` between turns or where the broker cannot."""
        hold_flow = getattr(self._target, "hold_flow", None)
        return None if hold_flow is None else cast("bool | None", await hold_flow(run_id=run_id, hold=hold))

    async def push_session_branch(self, grant: Callable[[str, str], Awaitable[None]]) -> object | None:
        """The push a person allowed, from the turn that holds the shelf now (a ``PushOutcome``), with a one-push grant
        ``grant`` registers; ``None`` between turns or without a pushable repository."""
        target = self._target
        push = getattr(target, "push_session_branch", None)
        return None if push is None else await push(grant)


@dataclass
class ExportJob:
    task_id: str
    requested: int
    args_digest: str
    status: ExportStatus = "working"
    records: int = 0
    path: str | None = None
    failure: str | None = None
    # Whether a status call has journaled its end (``finished``): once only.
    reported: bool = False
    _lines: list[str] = field(default_factory=list[str], repr=False)
    _task: asyncio.Task[None] | None = field(default=None, repr=False)

    @property
    def done(self) -> bool:
        return self.status in _DONE


class BulkExports:
    """One attempt's exports: each runs as its own task until it ends or the attempt closes them."""

    def __init__(
        self,
        shelf: ExportShelf,
        *,
        page_size: int = EXPORT_PAGE,
        max_bytes: int = MAX_EXPORT_BYTES,
        log: HarnessLog | None = None,
    ) -> None:
        self._shelf = shelf
        self._page = page_size
        self._max_bytes = max_bytes
        self._log = log or HarnessLog.named("harness_core.knowledge.bulk_export")
        self._jobs: dict[str, ExportJob] = {}

    def start(self, task_id: str, pages: RecordPages, requested: int, args_digest: str) -> ExportJob:
        existing = self._jobs.get(task_id)
        if existing is not None:
            return existing
        job = ExportJob(task_id, requested, args_digest)
        job._task = asyncio.ensure_future(self._run(job, pages))
        self._jobs[task_id] = job
        self._log.info("bulk_export.started", taskId=task_id, requested=requested)
        return job

    def get(self, task_id: str) -> ExportJob | None:
        return self._jobs.get(task_id)

    async def wait(self, job: ExportJob, timeout_s: float) -> None:
        """Until the job ends or ``timeout_s`` passes; its file written if a sandbox holds the shelf."""
        if job._task is not None and not job.done and timeout_s > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(job._task), timeout_s)
        if job.done:
            await self._write(job)

    async def close(self) -> None:
        """The attempt ended: every export still running stops (its reserve stays counted)."""
        for job in self._jobs.values():
            if job._task is not None and not job._task.done():
                job._task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await job._task
            if not job.done:
                # Cancelled before its first page ran: its own handler never saw the cancellation.
                job.status, job._lines = "cancelled", []

    async def _run(self, job: ExportJob, pages: RecordPages) -> None:
        cursor: str | None = None
        size = 0
        try:
            while job.records < job.requested:
                page = await pages(cursor, min(self._page, job.requested - job.records))
                for row in page.rows[: job.requested - job.records]:
                    line = json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
                    if size + len(line.encode()) > self._max_bytes:
                        job.status = "limited"
                        break
                    job._lines.append(line)
                    size += len(line.encode())
                    job.records += 1
                if job.status == "limited":
                    break
                cursor = page.next
                if cursor is None:
                    job.status = "completed"
                    break
            else:
                job.status = "limited" if cursor is not None or job.records >= job.requested else "completed"
        except asyncio.CancelledError:
            job.status, job._lines = "cancelled", []
            raise
        except Exception as error:  # a failed page ends the export with what it read so far discarded
            job.status, job.failure, job._lines, job.records = "failed", type(error).__name__, [], 0
            self._log.warning("bulk_export.failed", taskId=job.task_id, errorType=type(error).__name__)
            return
        self._log.info("bulk_export.ended", taskId=job.task_id, status=job.status, records=job.records, bytes=size)
        await self._write(job)

    async def _write(self, job: ExportJob) -> None:
        if job.path is not None or job.status not in ("completed", "limited"):
            return
        path = await self._shelf.write(export_file(job.task_id), "".join(job._lines))
        if path is not None:
            job.path, job._lines = path, []


def export_file(task_id: str) -> str:
    return _UNSAFE.sub("_", f"export-{task_id}")[:120]


@dataclass(frozen=True, slots=True)
class ExportToolNames:
    start: str
    status: str
    # The tool ids' source, as the host names its knowledge tools (``<source>@default/<tool>``).
    source: str


def export_tool_effect(names: ExportToolNames, name: str) -> ToolEffect | None:
    """The effect of one of the export tools (``None`` for any other name): a read of the person's own knowledge into
    the run's own sandbox, so nothing leaves the run."""
    if name not in (names.start, names.status):
        return None
    return ToolEffect(
        tool_id=tool_id_for(name.rpartition("__")[2], source=names.source),
        effect="read",
        idempotent=name == names.status,
        sink=SinkDescriptor(egress="none", args_leave_boundary=False),
    )


@dataclass(frozen=True, slots=True)
class ExportRefusal:
    """Why an export cannot start, as the model reads it."""

    message: str


@dataclass
class BulkExportTools:
    """The host's side of the export tools: where records come from, how much may be exported, the events."""

    names: ExportToolNames
    exports: BulkExports
    # The records of one export's arguments, or why it cannot run (a filter the scope refuses).
    pages_for: Callable[[Mapping[str, JsonValue]], Awaitable[RecordPages | ExportRefusal]]
    # The most records an export may write now (the host's allowance); never more than ``max_records``.
    allowance: Callable[[], int]
    # The host's export event for ``started``/``finished`` data, as it journals it.
    event: Callable[[dict[str, JsonValue]], NewEvent]
    # The most one export may ever write; ``allowance`` is the host's limit now, never above it.
    max_records: int = 100_000
    # Where the engine reads the shelf, as the host mounts it.
    shelf_path: str = "/knowledge"

    def schemas(self) -> list[ToolSchema]:
        return [
            ToolSchema(
                name=self.names.start,
                description=(
                    "Export many knowledge records the user can read (up to the export limit, 10,000 by default) "
                    f"to a JSONL file in {self.shelf_path}, in the background. "
                    "Use it when a task needs hundreds or thousands of records (counting, grouping, comparing); for a "
                    "few, search instead. It answers at once with a taskId; then call "
                    f"{self.names.status} with it until the status is completed, and read the file it names with "
                    "your shell (each line is one record's metadata: id, name, type, source, dates, link)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Only records whose name matches these words.", "maxLength": 500},
                        "source_ids": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 128},
                            "maxItems": 50,
                            "description": "Only these apps or collections (their ids).",
                        },
                        "record_types": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 64},
                            "maxItems": 20,
                            "description": "Only these record types (FILE, MAIL, TICKET, ...).",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": self.max_records,
                            "description": "The most records to export (default: as many as the limit allows).",
                        },
                    },
                    "additionalProperties": False,
                },
            ),
            ToolSchema(
                name=self.names.status,
                description=(
                    f"The status of an export {self.names.start} started: waits up to wait_seconds for it to end, "
                    "then says whether it is still working, how many records it wrote, and the file's path."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "minLength": 1, "maxLength": 160},
                        "wait_seconds": {"type": "integer", "minimum": 0, "maximum": MAX_WAIT_S},
                    },
                    "required": ["task_id"],
                    "additionalProperties": False,
                },
            ),
        ]

    def effect(self, name: str) -> ToolEffect | None:
        return export_tool_effect(self.names, name)

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        if call.name == self.names.start:
            return await self._start(call, ctx)
        return await self._status(call)

    async def _start(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        arguments = dict(call.arguments)
        allowance = min(self.allowance(), self.max_records)
        if allowance <= 0:
            return _error("No more records may be exported just now. Tell the user.")
        limit = arguments.get("limit", allowance)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            return _error("limit is a whole number of records, 1 or more.")
        requested = min(limit, allowance)
        pages = await self.pages_for(arguments)
        if isinstance(pages, ExportRefusal):
            return _error(pages.message)
        digest = canonical_sha256({k: v for k, v in arguments.items() if k != "limit"})
        task_id = _UNSAFE.sub("_", f"exp-{ctx.invocation_id}")[:128]
        job = self.exports.start(task_id, pages, requested, digest)
        answer: dict[str, JsonValue] = {
            "taskId": job.task_id,
            "status": job.status,
            "limit": requested,
            "file": f"{self.shelf_path}/{export_file(job.task_id)}.jsonl",
            "next": f"Call {self.names.status} with this taskId until the status is completed.",
        }
        if "limit" in arguments and requested < limit:
            answer["note"] = f"Limited to {requested:,} records by the export limits."
        started: dict[str, JsonValue] = {
            "taskId": job.task_id,
            "phase": "started",
            "records": requested,
            "requested": requested,
            "argsDigest": digest,
        }
        return ToolExecution("ok", json.dumps(answer, ensure_ascii=False), events=(self.event(started),))

    async def _status(self, call: ToolCall) -> ToolExecution:
        task_id = call.arguments.get("task_id")
        job = self.exports.get(task_id) if isinstance(task_id, str) else None
        if job is None:
            return _error(
                "No export with that taskId is running in this run: it ended with the run that started it. Start it "
                "again if you still need it."
            )
        wait = call.arguments.get("wait_seconds", DEFAULT_WAIT_S)
        await self.exports.wait(job, float(wait) if isinstance(wait, int) and not isinstance(wait, bool) else DEFAULT_WAIT_S)
        answer: dict[str, JsonValue] = {"taskId": job.task_id, "status": job.status, "records": job.records}
        events: tuple[NewEvent, ...] = ()
        if job.done:
            if job.path is not None:
                answer["file"] = job.path
            elif job.status in ("completed", "limited"):
                answer["status"] = "working"
                answer["note"] = "The export has ended; its file is written once the sandbox is ready. Call again."
            if job.status == "limited":
                answer["note"] = f"Stopped at {job.records:,} records; more match. Narrow the export to get the rest."
            if job.status == "failed":
                answer["note"] = "The export failed. Try again, or narrow it."
            if not job.reported and (job.path is not None or job.status in ("failed", "cancelled")):
                job.reported = True
                finished: dict[str, JsonValue] = {
                    "taskId": job.task_id,
                    "phase": "finished",
                    "records": job.records - job.requested,
                    "requested": job.requested,
                    "exported": job.records,
                    "outcome": job.status,
                    "argsDigest": job.args_digest,
                }
                events = (self.event(finished),)
        return ToolExecution("ok", json.dumps(answer, ensure_ascii=False), events=events)


def _error(message: str) -> ToolExecution:
    return ToolExecution("error", message, error_code="failed")


class ExportingCatalog:
    """The run's catalog with the export tools beside its own (``ToolCatalog``)."""

    def __init__(self, inner: ToolCatalog, tools: BulkExportTools) -> None:
        self._inner = inner
        self._tools = tools

    def describe_call(self, call: ToolCall) -> ToolEffect | None:
        return self._tools.effect(call.name) or self._inner.describe_call(call)

    def schemas(self) -> list[ToolSchema] | None:
        own = self._inner.schemas()
        taken = {schema.name for schema in own or ()}
        return [*(own or []), *(s for s in self._tools.schemas() if s.name not in taken)]

    async def execute(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        if self._tools.effect(call.name) is not None:
            return await self._tools.execute(call, ctx)
        return await self._inner.execute(call, ctx)


__all__ = [
    "DEFAULT_WAIT_S",
    "EXPORT_PAGE",
    "MAX_EXPORT_BYTES",
    "MAX_WAIT_S",
    "BulkExportTools",
    "BulkExports",
    "ExportJob",
    "ExportRefusal",
    "ExportShelf",
    "ExportStatus",
    "ExportToolNames",
    "ExportingCatalog",
    "RecordPage",
    "RecordPages",
    "ShelfRelay",
    "ShelvesFiles",
    "export_file",
    "export_tool_effect",
]
