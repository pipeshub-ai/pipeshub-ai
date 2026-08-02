"""Cross-worker sharing of expensive session-scoped resources under pytest-xdist.

Run serially, a ``scope="session"`` fixture is built exactly once -- which is what
the org-level fixtures here assume. Under ``-n N`` every xdist worker is its own
process with its own session, so a fixture that seeds an *org-wide singleton*
(the default LLM / embedding written to Configuration Manager) would be seeded
and torn down N times concurrently, and one worker's teardown would delete the
model another worker is mid-stream on.

``shared_session_resource`` elects exactly one worker to build the resource,
writes its serialized form into the directory shared by every worker in the run,
and hands the rest a rehydrated copy.

Teardown is deliberately **skipped** under xdist. The only safe moment to delete
an org-wide singleton is once every worker is done with it, and pytest offers no
cross-worker barrier at session end -- a refcount race would let the first worker
to finish delete a KB that a slower worker is still querying. CI tears the whole
docker stack down immediately after the pytest step, so the residue is collected
there. Running without ``-n`` keeps the original create/destroy behaviour intact,
which is what local runs against a long-lived server rely on.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

import pytest
from filelock import FileLock

logger = logging.getLogger(__name__)

T = TypeVar("T")


def get_worker_id(config: pytest.Config) -> str:
    """Return the xdist worker id (``gw0``, ``gw1``, ...) or ``master`` if serial."""
    return getattr(config, "workerinput", {}).get("workerid", "master")


def is_distributed(config: pytest.Config) -> bool:
    """True when this process is an xdist worker rather than a plain pytest run."""
    return get_worker_id(config) != "master"


@contextmanager
def shared_session_resource(
    name: str,
    *,
    config: pytest.Config,
    tmp_path_factory: pytest.TempPathFactory,
    create: Callable[[], T],
    destroy: Callable[[T], None],
    dump: Callable[[T], Any],
    load: Callable[[Any], T],
) -> Iterator[T]:
    """Build ``name`` once per run and share it across all xdist workers.

    ``create``/``destroy`` are the original fixture body and teardown. ``dump``
    and ``load`` convert the resource to and from JSON so non-electing workers
    can rehydrate it without touching the backend -- they must round-trip
    everything ``destroy`` and the tests need (ids, keys), not live objects.
    """
    worker_id = get_worker_id(config)

    # Serial run: no sharing needed, preserve the original create/destroy pair.
    if not is_distributed(config):
        resource = create()
        try:
            yield resource
        finally:
            destroy(resource)
        return

    # Each worker's basetemp is <root>/popen-gwN; the parent is common to the run.
    root = tmp_path_factory.getbasetemp().parent
    state_file = root / f"{name}.json"

    with FileLock(str(root / f"{name}.lock")):
        if state_file.is_file():
            resource = load(json.loads(state_file.read_text()))
            logger.info("[%s] reusing shared %r built by another worker", worker_id, name)
        else:
            resource = create()
            state_file.write_text(json.dumps(dump(resource)))
            logger.info("[%s] built shared %r for this run", worker_id, name)

    # Intentionally no teardown -- see module docstring.
    yield resource
