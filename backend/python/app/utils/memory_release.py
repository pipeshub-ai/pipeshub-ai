"""Handing the heap a finished job freed back to the OS."""
from __future__ import annotations

import contextlib
import ctypes


def release_freed_memory() -> None:
    """glibc keeps freed heap for reuse, so a long-lived process holds on to the
    peak of its largest job: the Docling service kept ~0.5 GB after each
    conversion of a 59 MB PDF, on top of what the next one needed."""
    with contextlib.suppress(OSError, AttributeError):  # not glibc (macOS, musl)
        ctypes.CDLL("libc.so.6").malloc_trim(0)
