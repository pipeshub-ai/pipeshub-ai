"""Stand in for a sync running in this process while a cached connector is evicted.

`evict_cached_connector` defers the dispose until the local run ends; these
helpers drive that end explicitly, so tests never wait on the real poll interval.
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
    """Yield a handle whose `running` flag is what `_running_here` answers."""
    state = SimpleNamespace(running=running, before=set(event_service._evict_tasks))
    with patch(f"{_MODULE}._running_here", side_effect=lambda _cid: state.running), \
         patch(f"{_MODULE}._reindexing_here", return_value=False), \
         patch(f"{_MODULE}._DEFERRED_DISPOSE_POLL_SEC", 0):
        yield state


async def end_local_run(state: SimpleNamespace) -> None:
    """End the run and let the disposes it deferred finish."""
    state.running = False
    await asyncio.gather(*(event_service._evict_tasks - state.before))
