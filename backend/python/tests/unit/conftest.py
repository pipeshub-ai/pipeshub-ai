import gc
import sys
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _fresh_connector_name_cache() -> None:
    """Connector names are cached per process; a test must not see another's."""
    chat_helpers = sys.modules.get("app.utils.chat_helpers")
    if chat_helpers is not None:
        chat_helpers._connector_name_cache.clear()
        chat_helpers._connector_name_misses.clear()


@pytest.fixture
def frozen_heap() -> Iterator[None]:
    """Keep a full GC pass over the suite's large heap out of the measurement.

    A generation-2 collection holds the GIL for as long as the heap is big, whichever
    thread triggers it; that is a property of the test process, not of the code under test.
    The heap is frozen without collecting it first: late in a shard that collection alone
    takes about a second.
    """
    gc.freeze()
    yield
    gc.unfreeze()
