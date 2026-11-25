"""The Invoker: one enforcement point for a run's tool calls (P2.3c).

Product-neutral: ids are opaque and injected (``IdSource``), no clock, no
randomness. Port: ``ToolCatalog`` (the run's grant). Adapters:
``InMemoryToolCatalog`` here, ``harness_core.tools.durable_port.RegistryToolPort``
over a ``ToolRegistry``. Contract suite: ``harness_core.testing.contracts.tool_catalog``.
Effects are recorded and executed by ``harness_core.effects.EffectRunner``.
"""

from harness_core.invoker.invoker import INVOCATION_PREFIX, Invoker, idempotency_key
from harness_core.invoker.memory import CatalogTool, InMemoryToolCatalog
from harness_core.invoker.ports import ToolCatalog
from harness_core.invoker.types import (
    NOT_GRANTED,
    InvocationScope,
    InvokeResult,
    RefusalReason,
    ToolRefusal,
)

__all__ = [
    "INVOCATION_PREFIX",
    "NOT_GRANTED",
    "CatalogTool",
    "InMemoryToolCatalog",
    "InvocationScope",
    "InvokeResult",
    "Invoker",
    "RefusalReason",
    "ToolCatalog",
    "ToolRefusal",
    "idempotency_key",
]
