"""How many indexing worker processes run side by side, for code that cannot import ``indexing_main``."""

from __future__ import annotations

import os


def indexing_worker_count() -> int:
    """Uvicorn worker processes of the indexing service (each with its own consumers)."""
    return max(1, int(os.getenv("INDEXING_UVICORN_WORKERS", "1")))
