import contextlib
import gc
import os
import shutil
import sys
import tempfile
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


_LEAK_PREFIX = "pipeshub_"


@pytest.fixture(scope="session", autouse=True)
def _private_temp_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Give each pytest process (each xdist worker) its own ``tempfile`` directory.

    Product code writes ``pipeshub_*`` scratch files with ``tempfile``. In the shared
    system temp dir a test could pass or fail on what other runs left there, and a leak
    could not be told from a concurrent process. A private directory removes both.
    """
    # pytest places its base temp dir on first use, under tempfile's directory: fix it
    # first, or tmp_path would land in the private dir and be removed with it, losing
    # the directories pytest keeps from failed tests.
    tmp_path_factory.getbasetemp()
    previous = tempfile.tempdir
    # Short and directly under the system temp dir (not under pytest's basetemp), so
    # AF_UNIX socket paths that tests build below it keep their headroom (107 chars).
    private = tempfile.mkdtemp(prefix="pt")
    tempfile.tempdir = private
    try:
        yield private
    finally:
        tempfile.tempdir = previous
        shutil.rmtree(private, ignore_errors=True)


@pytest.fixture(autouse=True)
def _no_leaked_pipeshub_temp_files(_private_temp_dir: str) -> Iterator[None]:
    """Fail a test that leaves a ``pipeshub_*`` file or directory in the temp directory.

    Tests in one process run one at a time, so any new entry is this test's. The leak is
    removed so it does not fail the tests after it.
    """
    before = set(os.listdir(_private_temp_dir))
    yield
    leaked = sorted(
        name
        for name in set(os.listdir(_private_temp_dir)) - before
        if name.startswith(_LEAK_PREFIX)
    )
    for name in leaked:
        path = os.path.join(_private_temp_dir, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                os.remove(path)
    assert not leaked, (
        f"test left temp files behind: {leaked}. Remove them in the test "
        "(or fix the code that creates them)."
    )
