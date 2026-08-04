import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Lock
from typing import Any, Callable


class TrackedThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._futures: set[Future[Any]] = set()
        self._futures_lock = Lock()

    def submit(
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future[Any]:
        with self._futures_lock:
            future = super().submit(fn, *args, **kwargs)
            self._futures.add(future)
        future.add_done_callback(self._discard_future)
        return future

    def _discard_future(self, future: Future[Any]) -> None:
        with self._futures_lock:
            self._futures.discard(future)

    async def shutdown_and_drain(self) -> None:
        with self._futures_lock:
            self.shutdown(wait=False, cancel_futures=True)
            futures = tuple(self._futures)
        if futures:
            await asyncio.gather(
                *(asyncio.wrap_future(future) for future in futures),
                return_exceptions=True,
            )
