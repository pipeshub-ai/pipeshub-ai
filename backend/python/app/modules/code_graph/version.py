"""What version of the code graph a block or a build was made by (O-10).

Two versions, because the two kinds of change cost differently:

- PARSER_VERSION: what parsing and projection put in blocks and blobs (a new
  fact, a renamed symbol, a new field). Blocks carry it as ``parserVersion``
  and blobs as ``code_facts_version``; blocks of another version are
  re-indexed by the upgrade backfill.
- EDGE_RULES_VERSION: how stored facts resolve to edges. A repo whose last
  full edge build ran under another one gets a full edge rebuild from its
  stored blocks and blobs -- no re-index, no LLM.

Both were one CODE_GRAPH_VERSION up to 2026.10.19; values stored under it are
plain strings compared for equality, so nothing old fails to load.
"""
from __future__ import annotations

__all__ = ["EDGE_RULES_VERSION", "PARSER_VERSION"]

# 2026.10.17: owner-qualified Go/C++ methods, name-matched calls for more
# languages, JSX/require/decorator/annotation facts, nameFolded.
# 2026.10.18: unnamed spans keyed by scope + content hash, not line range (P-10);
# code embedding units (SC-9).
# 2026.10.19: Java imports, heritage and typed receivers; Go receiver types;
# a package __init__.py keeps its imports on its nodes (re-exports).
# Left at the last CODE_GRAPH_VERSION so indexed repos are not re-indexed; the
# EXTENDS facts their blobs hold are read as INHERITS by the edge builder.
PARSER_VERSION = "2026.10.19"

# 2026.10.20: EXTENDS merged into INHERITS (an interface extending one stays
# INHERITS); the full rebuild this forces replaces stored EXTENDS edges.
EDGE_RULES_VERSION = "2026.10.20"
