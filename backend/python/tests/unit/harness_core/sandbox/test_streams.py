"""Spooling an archive stream: what fits in memory is written in place, what rolls over to disk is written off
the event loop, and the size cap still refuses."""

from __future__ import annotations

import io
import threading

import pytest

from harness_core.sandbox.errors import InvalidSandboxRequest
from harness_core.sandbox.streams import iter_chunks, spool


class _RecordingFile(io.BytesIO):
    """A spool file that records which thread made each write."""

    def __init__(self) -> None:
        super().__init__()
        self.writers: list[int] = []

    def write(self, data: bytes, /) -> int:  # type: ignore[override]
        self.writers.append(threading.get_ident())
        return super().write(data)


async def test_m7_2_writes_past_the_in_memory_threshold_run_off_the_event_loop() -> None:
    file = _RecordingFile()
    chunks = [b"a" * 4, b"b" * 4, b"c" * 4, b"d" * 4]

    size = await spool(iter_chunks(chunks), file, in_memory_bytes=8)

    loop_thread = threading.get_ident()
    assert size == 16
    assert file.read() == b"".join(chunks)
    assert [writer == loop_thread for writer in file.writers] == [True, True, False, False]


async def test_m7_2_a_stream_over_the_cap_is_refused() -> None:
    with pytest.raises(InvalidSandboxRequest, match="exceeds 10 bytes"):
        _ = await spool(iter_chunks([b"x" * 6, b"y" * 6]), io.BytesIO(), in_memory_bytes=4, max_bytes=10)
