import asyncio
import contextlib
import contextvars
import logging
import multiprocessing
import os
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from functools import lru_cache
from io import BytesIO
from typing import TYPE_CHECKING

from docling.backend.pypdfium2_backend import PyPdfiumDocumentBackend
from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.pipeline_options import (
    TableFormerMode,
    TableStructureOptions,
    ThreadedPdfPipelineOptions,
)
from docling.document_converter import (
    DocumentConverter,
    MarkdownFormatOption,
    PdfFormatOption,
    WordFormatOption,
)
from docling_core.types.doc.document import DoclingDocument

if TYPE_CHECKING:
    from docling.datamodel.document import ConversionResult

    from app.services.resource_governor import ResourceGovernor

from app.exceptions.indexing_exceptions import DocumentProcessingError
from app.models.blocks import BlocksContainer
from app.services.resource_governor.lanes import (
    DOCLING_OTHER_WEIGHT,
    DOCLING_WORKERS_ENV,
    LanePlan,
    docling_auto,
    docling_auto_width,
    docling_pdf_weight,
    explicit_setting,
    parse_lanes_mode,
)
from app.utils.converters.docling_doc_to_blocks import DoclingDocToBlocksConverter
from app.utils.pdf_utils import PAGE_BATCH_SIZE, get_pdf_page_count  # noqa: F401 - re-exported
from app.utils.pool_worker import run_and_release, start_housekeeping
from app.utils.worker_scaling import scaled

SUCCESS_STATUS = "success"

# The Docling service's PDF_PARSING_TIMEOUT_SECONDS. It gives up on a request
# then, but a conversion runs in a thread that cannot be cancelled: without
# Docling stopping itself it kept converting, holding its memory, while the
# freed gate admitted the next.
DOCUMENT_TIMEOUT_SECONDS = 40 * 60

# The only formats callers hand to DoclingProcessor (see LocalDoclingParser).
DOCLING_ROUTED_FORMATS = (InputFormat.PDF, InputFormat.DOCX, InputFormat.PPTX, InputFormat.MD)

# Wired by each service's lifespan (see parsing_main.py/indexing_main.py/
# docling_main.py) once a ResourceGovernor is constructed for that process —
# mirrors pdf_rasterizer.py's identical singleton and for the same reason:
# this module is a leaf shared across services with no DI path to whichever
# governor its caller's process owns.
_resource_governor: "ResourceGovernor | None" = None


def set_resource_governor(governor: "ResourceGovernor") -> None:
    """Wire an initialized ResourceGovernor so a worker OOM-kill can trigger
    its fast incident path instead of waiting for the next periodic sample."""
    globals()["_resource_governor"] = governor

DEFAULT_PAGE_BATCH_SIZE = 10


def _get_page_batch_size() -> int:
    raw = os.getenv("DOCLING_PAGE_BATCH_SIZE")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return DEFAULT_PAGE_BATCH_SIZE


PAGE_BATCH_SIZE = _get_page_batch_size()


def _get_local_parse_worker_count() -> int:
    raw_value = os.getenv("LOCAL_DOCLING_PARSE_WORKERS")
    if raw_value:
        try:
            return max(1, int(raw_value))
        except ValueError:
            return 1

    return 1


LOCAL_DOCLING_PARSE_WORKERS = _get_local_parse_worker_count()


@lru_cache(maxsize=1)
def _get_process_pool() -> ProcessPoolExecutor:
    return ProcessPoolExecutor(
        max_workers=scaled(LOCAL_DOCLING_PARSE_WORKERS),
        mp_context=multiprocessing.get_context("spawn"),
        initializer=start_housekeeping,
        initargs=(os.getpid(),),
    )



@lru_cache(maxsize=1)
def _get_converter() -> DocumentConverter:
    # Stage batch sizes and queue depth are held far below docling's defaults (4 / 100)
    # to cap how many pages the threaded pipeline keeps in flight, trading throughput
    # for a lower CPU and RAM ceiling on low-resource deployments.
    pipeline_options = ThreadedPdfPipelineOptions(
        do_ocr=False,
        table_structure_options=TableStructureOptions(
            mode=TableFormerMode.FAST,
        ),
        generate_picture_images=True,
        layout_batch_size=1,
        table_batch_size=1,
        queue_max_size=10,
        document_timeout=DOCUMENT_TIMEOUT_SECONDS,
    )
    # Docling picks the backend from the bytes before the file name, so without this
    # list a record routed here as PDF/DOCX/PPTX/MD could reach the JSON, METS, HTML
    # or LaTeX backends just by looking like one of those formats.
    return DocumentConverter(
        allowed_formats=list(DOCLING_ROUTED_FORMATS),
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options, backend=PyPdfiumDocumentBackend),
            InputFormat.DOCX: WordFormatOption(),
            InputFormat.MD: MarkdownFormatOption(),
        },
    )


def _parse_document_in_worker(
    doc_name: str, content: bytes, page_range: tuple[int, int] | None = None
) -> str:
    source = DocumentStream(name=doc_name, stream=BytesIO(content))
    kwargs: dict = {}
    if page_range is not None:
        kwargs["page_range"] = page_range
    conv_res: ConversionResult = _get_converter().convert(source, **kwargs)
    conv_res.input._backend.unload()
    if conv_res.status.value != SUCCESS_STATUS:
        raise DocumentProcessingError(
            f"Failed to parse document: {conv_res.status}",
            details={"status": str(conv_res.status)},
        )

    return conv_res.document.model_dump_json()


# -- the lane budget (PARSE_LANES=on) -----------------------------------------------

# The auto executor's children exit after this long without a conversion.
AUTO_IDLE_SECONDS = 60.0
AUTO_IDLE_TICK_SECONDS = 5.0

# A caller's own bounds on the lease wait, so parse_document keeps its signature.
_lease_wait: contextvars.ContextVar[float | None] = contextvars.ContextVar("docling_lease_wait", default=None)
_lease_cancelled: contextvars.ContextVar[Callable[[], bool] | None] = contextvars.ContextVar(
    "docling_lease_cancelled", default=None
)


@contextlib.contextmanager
def lease_scope(wait: float | None = None, cancelled: Callable[[], bool] | None = None) -> Iterator[None]:
    """Bound the lease wait of conversions started in this context, and let *cancelled*
    (a caller that went away) end it. Ignored when the lane budget is off."""
    tokens = (_lease_wait.set(wait), _lease_cancelled.set(cancelled))
    try:
        yield
    finally:
        _lease_cancelled.reset(tokens[1])
        _lease_wait.reset(tokens[0])


def lanes_active() -> bool:
    if parse_lanes_mode() != "on":
        return False
    governor = _resource_governor
    return governor is not None and getattr(governor, "lanes", None) is not None


def conversion_weight(content: bytes, plan: LanePlan) -> int:
    """Lease units for one conversion. The bytes decide, not the name; a PDF's 3 units are
    capped at ``lanes - 1`` (at least 1) so a small plan keeps lanes for the other conversions."""
    return docling_pdf_weight(plan) if content[:5] == b"%PDF-" else DOCLING_OTHER_WEIGHT


def _lane_mode() -> str | None:
    """``"thread"`` or ``"executor"`` when conversions take a lease, None for the legacy code.

    An explicit ``LOCAL_DOCLING_PARSE_WORKERS`` above 1 is a private pool the operator
    asked for: no lease. ``auto`` is an executor of ``(plan.lanes - 1) // 3`` children
    when that is at least 2; the shipped value 1 is the leased thread path.
    """
    if not lanes_active():
        return None
    if explicit_setting(DOCLING_WORKERS_ENV) is not None:
        return None
    if docling_auto() and docling_auto_width(_resource_governor.lanes.plan) >= 2:  # type: ignore[union-attr]
        return "executor"
    return "thread"


class _Leases:
    """The leases held by conversions in this process, for ``/health``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: dict[int, tuple[int, float]] = {}

    def add(self, key: int, weight: int) -> None:
        with self._lock:
            self._held[key] = (weight, time.monotonic())

    def drop(self, key: int) -> None:
        with self._lock:
            self._held.pop(key, None)

    def snapshot(self) -> tuple[int, int, float]:
        with self._lock:
            now = time.monotonic()
            return (
                len(self._held),
                sum(w for w, _ in self._held.values()),
                round(max((now - t for _, t in self._held.values()), default=0.0), 1),
            )


_leases = _Leases()


class _Held:
    """A lease released once, by whichever of the work ending and a cancelled start comes first."""

    def __init__(self, lease: object, weight: int) -> None:
        self._lease = lease
        self._key = id(self)
        _leases.add(self._key, weight)

    def release(self) -> None:
        _leases.drop(self._key)
        self._lease.release()  # type: ignore[attr-defined]


class _DoclingExecutor:
    """The ``auto`` executor: children start on demand and exit after ``idle`` seconds.

    One lock covers the in-flight count, creation and shutdown, so an idle shutdown
    cannot take the executor from under a submit.
    """

    def __init__(
        self,
        width: int,
        *,
        idle: float = AUTO_IDLE_SECONDS,
        tick: float = AUTO_IDLE_TICK_SECONDS,
        factory: Callable[[int], ProcessPoolExecutor] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.width = width
        self._idle, self._tick, self._clock = idle, tick, clock
        self._factory = factory or self._default_factory
        self._lock = threading.Lock()
        self._executor: ProcessPoolExecutor | None = None
        self._inflight = 0
        self._last_used = clock()
        self._stopped = threading.Event()
        self._timer: threading.Thread | None = None

    @staticmethod
    def _default_factory(width: int) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(
            max_workers=width,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=start_housekeeping,
            initargs=(os.getpid(),),
        )

    def submit(self, fn: Callable[..., object], *args: object) -> Future:
        with self._lock:
            if self._stopped.is_set():
                raise RuntimeError("the Docling executor is shut down")
            if self._executor is None:
                self._executor = self._factory(self.width)
            if self._timer is None:
                self._timer = threading.Thread(target=self._run_timer, name="docling-idle", daemon=True)
                self._timer.start()
            self._inflight += 1
            self._last_used = self._clock()
            try:
                future = self._executor.submit(fn, *args)
            except BaseException:
                self._inflight -= 1
                raise
        future.add_done_callback(self._finished)
        return future

    def _finished(self, _future: Future) -> None:
        with self._lock:
            self._inflight -= 1
            self._last_used = self._clock()

    def _run_timer(self) -> None:
        while not self._stopped.wait(self._tick):
            self.reap_if_idle()

    def reap_if_idle(self) -> bool:
        with self._lock:
            if self._executor is None or self._inflight > 0 or self._clock() - self._last_used < self._idle:
                return False
            executor, self._executor = self._executor, None
            executor.shutdown(wait=True)
            return True

    def drop_broken(self) -> None:
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)

    def children(self) -> int:
        with self._lock:
            return len(getattr(self._executor, "_processes", None) or ())

    def shutdown(self) -> None:
        self._stopped.set()
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


_auto_lock = threading.Lock()
_auto: list[_DoclingExecutor | None] = [None]  # a list so no global statement is needed


def _get_auto_executor(width: int) -> _DoclingExecutor:
    with _auto_lock:
        current = _auto[0]
        if current is None or current.width != width:
            current = _auto[0] = _DoclingExecutor(width)
        return current


def shutdown_docling_pool() -> bool:
    """Stop the ``auto`` executor (if one exists). Call before the governor closes.

    Does nothing for the legacy and explicit private pools, which keep their own exit.
    """
    with _auto_lock:
        executor, _auto[0] = _auto[0], None
    if executor is None:
        return False
    executor.shutdown()
    return True


def docling_lane_stats() -> dict[str, object]:
    """The ``docling`` block of the ``lanes`` health report."""
    held, weight, oldest = _leases.snapshot()
    mode = _lane_mode() if lanes_active() else None
    explicit = explicit_setting(DOCLING_WORKERS_ENV)
    executor = _auto[0]
    return {
        "leases_held": held,
        "weight_held": weight,
        "oldest_age_seconds": oldest,
        "mode": mode or ("private" if explicit is not None and lanes_active() else "off"),
        "width": executor.width if executor is not None else (explicit or 1),
        "children": executor.children() if executor is not None else 0,
    }


async def _parse_on_lanes(
    processor: "DoclingProcessor",
    mode: str,
    doc_name: str,
    content: bytes | BytesIO,
    page_range: tuple[int, int] | None,
) -> DoclingDocument:
    raw_content = content.getvalue() if isinstance(content, BytesIO) else content
    budget = _resource_governor.lanes  # type: ignore[union-attr]
    weight = conversion_weight(raw_content, budget.plan)
    wait = _lease_wait.get()
    if wait is None:
        from app.modules.parsers.parse_pool_lanes import lane_wait_seconds

        wait = lane_wait_seconds()
    lease = _Held(
        await budget.acquire_async(cancelled=_lease_cancelled.get(), weight=weight, timeout=wait),
        weight,
    )
    # Each path ties the lease's release to the end of its work, and gives it back
    # itself when it fails before that work exists.
    if mode == "executor":
        return await _convert_in_executor(processor, lease, doc_name, raw_content, page_range)
    return await _convert_in_thread(processor, lease, doc_name, raw_content, page_range)


async def _convert_in_thread(
    processor: "DoclingProcessor", lease: _Held, doc_name: str, raw: bytes, page_range: tuple[int, int] | None
) -> DoclingDocument:
    try:
        source = DocumentStream(name=doc_name, stream=BytesIO(raw))
    except BaseException:
        lease.release()
        raise
    kwargs: dict = {}
    if page_range is not None:
        kwargs["page_range"] = page_range
    state = threading.Lock()
    begun = skipped = False

    def job() -> object:
        nonlocal begun
        with state:
            if skipped:
                return None
            begun = True
        return processor.converter.convert(source, **kwargs)

    loop = asyncio.get_running_loop()
    try:
        fut = loop.run_in_executor(None, job)
    except BaseException:
        lease.release()
        raise
    fut.add_done_callback(lambda _f: lease.release())
    try:
        conv_res: ConversionResult = await asyncio.shield(fut)
    except asyncio.CancelledError:
        # A conversion that has not started will not: free its lease now. One that
        # has keeps it until it ends (the callback), whatever happened to this awaiter.
        with state:
            if not begun:
                skipped = True
                lease.release()
        raise
    conv_res.input._backend.unload()
    if conv_res.status.value != SUCCESS_STATUS:
        raise DocumentProcessingError(
            f"Failed to parse document: {conv_res.status}",
            details={"status": str(conv_res.status)},
        )
    return conv_res.document


async def _convert_in_executor(
    processor: "DoclingProcessor", lease: _Held, doc_name: str, raw: bytes, page_range: tuple[int, int] | None
) -> DoclingDocument:
    executor = _get_auto_executor(docling_auto_width(_resource_governor.lanes.plan))  # type: ignore[union-attr]
    try:
        cf = executor.submit(run_and_release, _parse_document_in_worker, doc_name, raw, page_range)
    except BaseException:
        lease.release()
        raise
    cf.add_done_callback(lambda _f: lease.release())
    try:
        serialized_doc = await asyncio.wrap_future(cf)
    except BrokenProcessPool:
        processor.logger.warning(
            "Docling executor broke while parsing '%s' (worker likely OOM-killed); recreating it",
            doc_name,
        )
        executor.drop_broken()
        if _resource_governor is not None:
            _resource_governor.report_memory_incident("docling worker OOM-killed (BrokenProcessPool)")
        raise
    return DoclingDocument.model_validate_json(serialized_doc)


class DoclingProcessor():
    def __init__(self, logger: logging.Logger, config: object) -> None:
        self.logger = logger
        self.config = config
        self.converter = _get_converter()

    async def parse_document(
        self,
        doc_name: str,
        content: bytes | BytesIO,
        page_range: tuple[int, int] | None = None,
    ) -> DoclingDocument:
        """Parse document and return raw Docling result (no block conversion).

        Args:
            page_range: Optional 1-based inclusive (start, end) page range.
        """
        if (mode := _lane_mode()) is not None:
            return await _parse_on_lanes(self, mode, doc_name, content, page_range)
        raw_content = content.getvalue() if isinstance(content, BytesIO) else content

        if LOCAL_DOCLING_PARSE_WORKERS > 1:
            loop = asyncio.get_running_loop()
            try:
                serialized_doc = await loop.run_in_executor(
                    _get_process_pool(),
                    run_and_release,
                    _parse_document_in_worker,
                    doc_name,
                    raw_content,
                    page_range,
                )
            except BrokenProcessPool:
                self.logger.warning(
                    "Docling process pool broke while parsing '%s' (worker "
                    "likely OOM-killed); recreating pool",
                    doc_name,
                )
                _get_process_pool.cache_clear()
                if _resource_governor is not None:
                    # Same rationale as pdf_rasterizer.py: a worker OOM-kill
                    # is proof of memory exhaustion the periodic sampler may
                    # not see for several seconds — react now instead of
                    # letting admission keep granting heavy-parse slots at
                    # the limit that just caused this kill.
                    _resource_governor.report_memory_incident(
                        "docling worker OOM-killed (BrokenProcessPool)"
                    )
                raise
            return DoclingDocument.model_validate_json(serialized_doc)

        source = DocumentStream(name=doc_name, stream=BytesIO(raw_content))
        kwargs: dict = {}
        if page_range is not None:
            kwargs["page_range"] = page_range
        conv_res: ConversionResult = await asyncio.to_thread(
            self.converter.convert, source, **kwargs
        )
        conv_res.input._backend.unload()
        if conv_res.status.value != SUCCESS_STATUS:
            raise DocumentProcessingError(
                f"Failed to parse document: {conv_res.status}",
                details={"status": str(conv_res.status)},
            )

        return conv_res.document

    async def create_blocks(
        self,
        doc: DoclingDocument,
        page_number: int | None = None,
        skip_table_enrichment: bool = False,
    ) -> BlocksContainer:
        """Convert parsed Docling result to BlocksContainer.

        This is the second phase - involves LLM calls for table processing,
        unless skip_table_enrichment is set.
        """
        doc_to_blocks_converter = DoclingDocToBlocksConverter(logger=self.logger, config=self.config)
        return await doc_to_blocks_converter.convert(
            doc, page_number=page_number, skip_table_enrichment=skip_table_enrichment
        )

    def process_document(self) -> None:
        pass



