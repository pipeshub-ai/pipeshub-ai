from __future__ import annotations

import asyncio
import threading

import pytest

from app.utils.cpu_offload import (
    DEFAULT_OFFLOAD_THRESHOLD_BYTES,
    offload_if_large,
)


def _thread_of(payload: object) -> str:
    return threading.current_thread().name


class TestOffloadIfLarge:
    @pytest.mark.asyncio
    async def test_small_payload_runs_inline(self) -> None:
        """A thread hop costs more than the work it would move for the few-KB
        records a connector sync emits in bulk."""
        caller = threading.current_thread().name

        assert await offload_if_large(_thread_of, b"tiny") == caller

    @pytest.mark.asyncio
    async def test_large_payload_leaves_the_event_loop(self) -> None:
        """The whole point: a big document must not block the single worker
        loop every other in-flight record shares."""
        caller = threading.current_thread().name
        payload = b"x" * (DEFAULT_OFFLOAD_THRESHOLD_BYTES + 1)

        assert await offload_if_large(_thread_of, payload) != caller

    @pytest.mark.asyncio
    async def test_threshold_is_inclusive(self) -> None:
        caller = threading.current_thread().name
        payload = b"x" * DEFAULT_OFFLOAD_THRESHOLD_BYTES

        assert await offload_if_large(_thread_of, payload) != caller

    @pytest.mark.asyncio
    async def test_str_payload_is_measured_too(self) -> None:
        caller = threading.current_thread().name
        payload = "x" * (DEFAULT_OFFLOAD_THRESHOLD_BYTES + 1)

        assert await offload_if_large(_thread_of, payload) != caller

    @pytest.mark.asyncio
    async def test_unmeasurable_payload_is_treated_as_large(self) -> None:
        """An unknown payload is more likely a document than a short string,
        and a needless thread hop is far cheaper than a stalled loop."""
        caller = threading.current_thread().name

        assert await offload_if_large(_thread_of, {"a": "dict"}) != caller

    @pytest.mark.asyncio
    async def test_result_and_arguments_pass_through_unchanged(self) -> None:
        def concat(a: bytes, b: bytes, c: bytes) -> bytes:
            return a + b + c

        big = b"x" * (DEFAULT_OFFLOAD_THRESHOLD_BYTES + 1)
        assert await offload_if_large(concat, big, b"-", b"end") == big + b"-end"
        assert await offload_if_large(concat, b"a", b"-", b"end") == b"a-end"

    @pytest.mark.asyncio
    async def test_exceptions_propagate_from_either_path(self) -> None:
        def boom(_payload: bytes) -> None:
            raise ValueError("kaboom")

        for payload in (b"small", b"x" * (DEFAULT_OFFLOAD_THRESHOLD_BYTES + 1)):
            with pytest.raises(ValueError, match="kaboom"):
                await offload_if_large(boom, payload)

    @pytest.mark.asyncio
    async def test_an_unsizable_payload_is_offloaded_above_any_threshold(self) -> None:
        """`_sizeof` reports the unknown as maxsize, not as the module default:
        a caller raising `threshold_bytes` past that default would otherwise
        flip "unknown" from large to small and run a document inline."""
        caller = threading.current_thread().name
        big_threshold = DEFAULT_OFFLOAD_THRESHOLD_BYTES * 64

        result = await offload_if_large(
            _thread_of, object(), threshold_bytes=big_threshold
        )

        assert result != caller

    async def test_sized_arg_overrides_the_first_argument(self) -> None:
        """Call sites whose first argument is a handle rather than the payload
        can still route correctly."""
        caller = threading.current_thread().name
        big = b"x" * (DEFAULT_OFFLOAD_THRESHOLD_BYTES + 1)

        result = await offload_if_large(_thread_of, b"tiny", sized_arg=big)

        assert result != caller

    @pytest.mark.asyncio
    async def test_offloaded_work_does_not_block_the_loop(self) -> None:
        """The behaviour that matters, asserted directly: while a large
        payload is being processed, other coroutines on this loop keep
        running."""
        started = threading.Event()
        release = threading.Event()
        ticks = 0

        def blocking(_payload: bytes) -> str:
            started.set()
            release.wait(timeout=5)
            return "done"

        async def tick() -> None:
            nonlocal ticks
            # Polling a threading.Event from the loop is the point: the work is
            # in another thread, so there is no asyncio primitive to await.
            while not started.is_set():  # noqa: ASYNC110
                await asyncio.sleep(0)
            for _ in range(5):
                ticks += 1
                await asyncio.sleep(0)
            release.set()

        payload = b"x" * (DEFAULT_OFFLOAD_THRESHOLD_BYTES + 1)
        ticker = asyncio.create_task(tick())
        assert await offload_if_large(blocking, payload) == "done"
        await ticker

        assert ticks == 5
