"""What version of the code graph a block or a build was made by (O-10).

Bump CODE_GRAPH_VERSION whenever parsing or projection changes what blocks
hold (a new fact, a renamed symbol, a new field): blocks of an older version
are re-indexed by the upgrade backfill, and a repo whose last build ran
under another version gets a full rebuild.
"""
from __future__ import annotations

__all__ = ["CODE_GRAPH_VERSION"]

# 2026.10.17: owner-qualified Go/C++ methods, name-matched calls for more
# languages, JSX/require/decorator/annotation facts, nameFolded.
CODE_GRAPH_VERSION = "2026.10.17"
