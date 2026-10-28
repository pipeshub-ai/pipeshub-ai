"""Stand in for a sync running in this process while a cached connector is evicted.

`evict_cached_connector` defers the dispose until the runs live at eviction end;
these helpers drive that end explicitly, so tests never wait on a real run.
"""

import asyncio
import contextlib
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import patch

from app.connectors.services import event_service

_MODULE = "app.connectors.services.event_service"


@contextlib.contextmanager
def local_run(*, running: bool = True) -> Iterator[SimpleNamespace]:
    """Yield a handle whose `running` flag is what `_running_here` answers.

    A run that is live when a connector is evicted is the one its dispose waits for.
    """
    state = SimpleNamespace(
        running=running, ended=asyncio.Event(), before=set(event_service._evict_tasks)
    )
    with patch(f"{_MODULE}._running_here", side_effect=lambda _cid: state.running), \
         patch(f"{_MODULE}._reindexing_here", return_value=False), \
         patch(f"{_MODULE}._runs_holding",
               side_effect=lambda _cid: [state.ended.wait()] if state.running else []):
        yield state


async def end_local_run(state: SimpleNamespace) -> None:
    """End the run and let the disposes it deferred finish."""
    state.running = False
    state.ended.set()
    await asyncio.gather(*(event_service._evict_tasks - state.before))
