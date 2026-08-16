import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from threading import Event

import pytest

from app.sources.external.google.admin.admin import GoogleAdminDataSource
from app.sources.external.google.drive.drive import GoogleDriveDataSource
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data_source_type",
    [GoogleAdminDataSource, GoogleDriveDataSource, GoogleGmailDataSource],
)
async def test_cancellation_does_not_release_transport(
    data_source_type: type,
) -> None:
    executor = ThreadPoolExecutor(max_workers=2)
    data_source = data_source_type(object(), executor=executor)
    first_started = Event()
    release_first = Event()
    second_started = Event()

    def first_operation() -> str:
        first_started.set()
        release_first.wait(timeout=2)
        return "first"

    def second_operation() -> str:
        second_started.set()
        return "second"

    try:
        first_task = asyncio.create_task(data_source.execute(first_operation))
        assert await asyncio.to_thread(first_started.wait, 1)

        first_task.cancel()
        with suppress(asyncio.CancelledError):
            await first_task

        second_task = asyncio.create_task(data_source.execute(second_operation))
        await asyncio.sleep(0.05)
        assert not second_started.is_set()

        release_first.set()
        assert await asyncio.wait_for(second_task, timeout=1) == "second"
    finally:
        release_first.set()
        executor.shutdown(wait=True, cancel_futures=True)
