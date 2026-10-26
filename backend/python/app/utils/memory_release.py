"""Handing the heap a finished job freed back to the OS."""
from __future__ import annotations

import contextlib
import ctypes
import os

_M_ARENA_MAX = -8  # glibc malloc.h


def release_freed_memory() -> None:
    """glibc keeps freed heap for reuse, so a long-lived process holds on to the
    peak of its largest job: the Docling service kept ~0.5 GB after each
    conversion of a 59 MB PDF, on top of what the next one needed."""
    with contextlib.suppress(OSError, AttributeError):  # not glibc (macOS, musl)
        ctypes.CDLL("libc.so.6").malloc_trim(0)


def limit_malloc_arenas(count: int) -> None:
    """Cap glibc's per-thread heaps ("arenas") at *count*, unless the operator
    set MALLOC_ARENA_MAX, which glibc has already applied.

    Call it before the process starts threads. A thread can get an arena of its
    own, and each holds freed memory and fragments apart from the others: the
    Docling service's memory after a trim crept up 1.0 → 1.2 GB over five
    conversions of the same PDF, and stayed at 0.78 GB with two arenas.
    """
    if "MALLOC_ARENA_MAX" in os.environ:
        return
    with contextlib.suppress(OSError, AttributeError):  # not glibc (macOS, musl)
        ctypes.CDLL("libc.so.6").mallopt(_M_ARENA_MAX, count)
