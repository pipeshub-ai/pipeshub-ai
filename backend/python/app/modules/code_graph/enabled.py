"""Kill switch for the code graph: CODE_GRAPH_ENABLED=false skips block projection,
edge build requests, builds, the reconciler and the upgrade backfill. Code files
are still indexed for search. Read once per process."""
from __future__ import annotations

import os

__all__ = ["CODE_GRAPH_ENABLED"]

# Only an explicit off disables it: Compose and Helm pass an unset value as "".
CODE_GRAPH_ENABLED = os.getenv("CODE_GRAPH_ENABLED", "").strip().lower() not in ("false", "0", "off")
