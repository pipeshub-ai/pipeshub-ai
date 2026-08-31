"""Scale per-process resource budgets by this service's uvicorn worker count.

Budgets like ``MAX_CONCURRENT_INDEXING_LLM_CALLS`` or a rasteriser process pool are
sized per interpreter. Run the service with N uvicorn workers and each of the N
processes claims the whole budget, so the process group oversubscribes by N — N times
the concurrent LLM calls against a provider that rate-limits, N process pools competing
for the same cores.

The count cannot be read from a single env var here, because the modules holding these
budgets are shared between the query, parsing, indexing and docling services and each
has its own ``*_UVICORN_WORKERS``. So a service declares its own count at startup and
the shared modules divide through :func:`scaled`.

Only the query service declares one today. Everything else leaves the default of 1, where
:func:`scaled` is the identity, so their budgets are unchanged.

This is the second mechanism in the tree for this concern: parsing, indexing and
docling already pass an explicit ``worker_count`` into ``ResourceGovernor``, which
divides *its* ceilings the same way. This one exists for budgets that live in
lazily-built module singletons, where there is no constructor to thread a count
through.

Note the division is per process, not per event loop: a process running several
loops gets one share each (see ``concurrency.py``'s per-loop semaphore).

Call :func:`scaled` lazily — at first use, not at import — because the count is set
during startup, after module imports have run.
"""

_state = {"workers": 1}


def set_process_worker_count(count: int) -> None:
    """Declare how many worker processes this service was started with."""
    _state["workers"] = max(1, int(count))


def get_process_worker_count() -> int:
    return _state["workers"]


def scaled(total: int) -> int:
    """Divide a whole-service budget into this process's share, never below 1."""
    return max(1, int(total) // _state["workers"])
