"""How many indexing worker processes run side by side, for code that cannot import ``indexing_main``."""

from __future__ import annotations

import os

from app.capacity.knobs import knob_value


def indexing_worker_count() -> int:
    """Uvicorn worker processes of the indexing service (each with its own consumers).

    ``INDEXING_UVICORN_WORKERS``, read through the knob registry so that a blank value means one.
    """
    value = knob_value(os.environ, "INDEXING_UVICORN_WORKERS")
    return max(1, int(value)) if value is not None else 1  # type: ignore[call-overload]
