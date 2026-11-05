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

A failed import raises and is not remembered, so the next call tries again.
"""
from __future__ import annotations

import asyncio
import importlib

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
    lock: asyncio.Lock | None = None
    lock_loop: asyncio.AbstractEventLoop | None = None


_state = _State()


def _import_docling() -> None:
    import app.utils.runtime_threads  # noqa: F401

    for name in _MODULES:
        importlib.import_module(name)


def _get_lock() -> asyncio.Lock:
    # One lock per running loop: a lock created under another loop (a new test loop, a
    # restarted server) is never reused.
    loop = asyncio.get_running_loop()
    if _state.lock is None or _state.lock_loop is not loop:
        _state.lock, _state.lock_loop = asyncio.Lock(), loop
    return _state.lock


async def ensure_docling() -> None:
    """Import ``docling`` in a worker thread, once per process."""
    if _state.done:
        return
    async with _get_lock():
        if _state.done:
            return
        await asyncio.to_thread(_import_docling)
        _state.done = True
