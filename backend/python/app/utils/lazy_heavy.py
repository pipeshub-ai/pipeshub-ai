"""Import ``docling`` (and with it ``torch`` and ``transformers``) on first use, off the event loop.

Importing ``docling`` takes 10 to 30 seconds and about 400 MiB on a small machine. Four
of the services never convert a document with it, and the rest convert only DOCX, PPTX or
Markdown now and then, so a service loads it when the first such document arrives.

The import must not run on the event loop: it would stall health checks, lease renewals
and every other request for its whole duration. :func:`ensure_docling` runs it in a worker
thread, once; concurrent first callers wait for that one import, and later callers return
at once. Any code that is about to use ``docling`` from async code awaits it first. Code
that runs in a worker thread already may import ``docling`` itself; Python's per-module
import lock makes that safe, but it must not run on the loop thread.

A failed import raises and is not remembered, so the next call tries again. A caller that
is cancelled leaves the import running for the others.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import threading
from concurrent.futures import Future

# Importing the runtime-thread settings first matches every service entry point: they must
# precede any import that starts native threads.
_MODULES = (
    "docling.datamodel.base_models",
    "docling.datamodel.document",
    "docling.datamodel.pipeline_options",
    "docling.backend.pypdfium2_backend",
    "docling.document_converter",
    "docling.exceptions",
)



class _State:
    done = False
    # The one import in flight, shared by every caller on every loop. It outlives a caller
    # that is cancelled, so a later caller joins it instead of starting a second import.
    inflight: Future[None] | None = None


_state = _State()
_guard = threading.Lock()


def _docling_is_loaded() -> bool:
    if _state.done:
        return True
    module = sys.modules.get("docling.document_converter")
    if module is None:
        return False
    # A module is in ``sys.modules`` from the moment its import starts, while another thread
    # may still be executing it; the import system clears this flag when it finishes.
    return not getattr(getattr(module, "__spec__", None), "_initializing", False)


def require_off_loop() -> None:
    """Raise when ``docling`` is not imported yet and the caller is on an event loop thread.

    The synchronous entry points that build a Docling converter (``converter`` properties)
    would import the library inline and stall the loop for the whole import. Reaching one
    from a coroutine without ``await ensure_docling()`` first is a bug; fail loudly.
    """
    if _docling_is_loaded():
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "docling is not imported yet: await ensure_docling() first, or call this from a worker thread"
    )


def _import_docling() -> None:
    import app.utils.runtime_threads  # noqa: F401

    for name in _MODULES:
        importlib.import_module(name)


def _run_import(future: Future[None]) -> None:
    if not future.set_running_or_notify_cancel():
        return
    try:
        _import_docling()
    except BaseException as exc:
        with _guard:
            _state.inflight = None  # a failure is not remembered: the next call tries again
        future.set_exception(exc)
        return
    with _guard:
        _state.done = True
        _state.inflight = None
    future.set_result(None)


def _start_or_join() -> Future[None]:
    with _guard:
        if _state.inflight is None:
            future: Future[None] = Future()
            _state.inflight = future
            threading.Thread(target=_run_import, args=(future,), name="docling-import", daemon=True).start()
        return _state.inflight


async def ensure_docling() -> None:
    """Import ``docling`` in a worker thread, once per process."""
    if _state.done:
        return
    # Shielded: cancelling this caller must not cancel the import the others are waiting on.
    await asyncio.shield(asyncio.wrap_future(_start_or_join()))
