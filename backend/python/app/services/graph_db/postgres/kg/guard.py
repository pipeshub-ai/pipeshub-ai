"""The in-process layer of the chokepoint: which provider method is writing, and is it classified (registry.py).

``instrument_writers`` wraps the public coroutine methods of the provider mixins to record the outermost method's name in a context variable
(copied into an explicit transaction's task by ``_Txn.run``). ``KernelDB.graph_write_hook`` calls :func:`check` before any statement that writes
graph.nodes or graph.edges. Strict mode (``PG_KG_GUARD=strict``, tests) raises for an unclassified writer; otherwise it is logged and counted:
the capture triggers still project its changes.
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import logging
import os
from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg.registry import (
    WRITERS,
    UnclassifiedProviderWrite,
)
from app.telemetry.backend import METRICS_BACKEND

if TYPE_CHECKING:
    from app.core.db import Tx

logger = logging.getLogger(__name__)
current_writer: contextvars.ContextVar[tuple[str, ...]] = contextvars.ContextVar("pg_provider_methods", default=())     # the public provider methods running, outermost first
UNCLASSIFIED = METRICS_BACKEND.counter("pipeshub_kg_unclassified_graph_writes_total", "Graph writes by a provider method the kg registry does not list", ["method"])


def strict() -> bool:
    from app.services.graph_db.postgres.kg.flush import check_env
    check_env()
    return os.environ.get("PG_KG_GUARD", "") == "strict"


def check(tx: Tx, sql: str) -> None:
    """Called before a statement that writes graph.nodes or graph.edges."""
    stack = current_writer.get()
    if any(n in WRITERS for n in stack):
        return
    label = stack[0] if stack else "<outside the provider>"
    if strict():
        raise UnclassifiedProviderWrite(f"{label} writes the graph and is not in the kg registry (app/services/graph_db/postgres/kg/registry.py)")
    UNCLASSIFIED.inc(label)
    logger.warning("graph write by %s is not classified for the kg projection; its changes are captured by the triggers", label)


def instrument_writers(cls: type) -> None:
    """Push the name of each public coroutine method of ``cls`` while it runs."""
    for name, fn in list(vars(cls).items()):
        if name.startswith("_") or not inspect.iscoroutinefunction(fn):
            continue

        def make(fn=fn, name=name):  # noqa: ANN001, ANN202
            @functools.wraps(fn)
            async def wrapper(*a, **k):  # noqa: ANN002, ANN003, ANN202
                token = current_writer.set((*current_writer.get(), name))
                try:
                    return await fn(*a, **k)
                finally:
                    current_writer.reset(token)
            return wrapper
        setattr(cls, name, make())
