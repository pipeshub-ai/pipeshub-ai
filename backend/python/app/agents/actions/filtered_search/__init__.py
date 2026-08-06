"""Hybrid native-filter + semantic-retrieval search.

Importing this package (or any submodule — Python always runs a package's
``__init__`` first) registers every built-in `NativeFilterAdapter` with
`FilterAdapterRegistry`, so `FilterAdapterRegistry.get(...)` is always safe
to call from any module in this subsystem without a separate bootstrap step.
"""

from app.agents.actions.filtered_search import (
    adapters,  # noqa: F401 — registers built-in adapters
)

__all__: list[str] = []
