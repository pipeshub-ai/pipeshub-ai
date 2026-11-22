"""What version of the code graph a block or a build was made by (O-10).

Three versions, because the kinds of change cost differently:

- PARSER_VERSION: what parsing and projection put in blocks and blobs (a new
  fact, a renamed symbol, a new field). Blocks carry it as ``parserVersion``
  and blobs as ``code_facts_version``; blocks of another version are
  re-indexed by the upgrade backfill.
- EDGE_RULES_VERSION: how stored facts resolve to edges. A repo whose last
  full edge build ran under another one gets a full edge rebuild from its
  stored blocks and blobs -- no re-index, no LLM.
- EMBED_UNITS_VERSION: what a code record embeds besides its blocks. Folded
  into code groups' content hashes, so a reconcile re-embeds only classes; the
  class-units backfill adds the units to records indexed before them.

The first two were one CODE_GRAPH_VERSION up to 2026.10.19; values stored under it are
plain strings compared for equality, so nothing old fails to load.
"""
from __future__ import annotations

__all__ = ["EDGE_RULES_VERSION", "EMBED_UNITS_VERSION", "PARSER_VERSION"]

# 2026.10.17: owner-qualified Go/C++ methods, name-matched calls for more
# languages, JSX/require/decorator/annotation facts, nameFolded.
# 2026.10.18: unnamed spans keyed by scope + content hash, not line range (P-10);
# code embedding units (SC-9).
# 2026.10.19: Java imports, heritage and typed receivers; Go receiver types;
# a package __init__.py keeps its imports on its nodes (re-exports).
# Left at the last CODE_GRAPH_VERSION so indexed repos are not re-indexed; the
# EXTENDS facts their blobs hold are read as INHERITS by the edge builder.
# 2026.11.8: Rust use paths, impls named by their type, traits, type table, callees, receiver chains, macro args.
# 2026.11.9: C/C++ includes, preprocessor branches, declarators, macros, static and C++ type facts.
# 2026.11.13: C# and PHP namespace, import, heritage and type-table facts; C# modifiers and typeParamCount.
# 2026.11.17: a function or method without a body is a declaration in every language, not only C/C++ and C#.
# 2026.11.19: an exported JS/TS module-level value is a definition; a bare call to a JS/TS parameter or local
# is no CALLS fact.
# 2026.11.20: Go embedded fields are heritage facts with context "embeds" (method promotion only).
# 2026.11.21: Python star imports, a literal `__all__`, scoped receiver types and chains, pytest fixtures.
# 2026.11.24: JS default imports are marked; a built-in-named JS call on an imported module is kept.
# 2026.11.25: Python and JS/TS INDIRECT_CALL facts for functions passed as values; accessors carry isAccessor.
PARSER_VERSION = "2026.11.25"

# 2026.10.20: EXTENDS merged into INHERITS (an interface extending one stays
# INHERITS); the full rebuild this forces replaces stored EXTENDS edges.
# 2026.11.8: Rust paths placed by a crate resolver, impls as parts of their type, typed receiver chains.
# 2026.11.9: C/C++ include scope, header/source pairing, wrapper macros, static linkage, C++ typed calls.
# 2026.11.13: C# and PHP resolvers; super calls skip interfaces.
# 2026.11.15: tsconfig/jsconfig `paths` place JS/TS imports; module bindings and module-level values in calls.
# 2026.11.16: Go embedding promotes the embedded type's methods, depth by depth.
# 2026.11.21: Python import scoping, star re-exports, function-local imports as the file's, scoped receivers.
# 2026.11.24: blocks carry externalRefs, the library calls they make (`requests:get`).
# 2026.11.25: INDIRECT_CALL edges to functions and methods passed as values (never to an accessor).
EDGE_RULES_VERSION = "2026.11.25"

# What a code record embeds besides its blocks (class header units, the residue unit). It is folded into code
# groups' content hashes only, so a bump re-indexes each class while unchanged blocks keep their vectors.
# 2026.11.25: class header units and one residue unit per code file.
EMBED_UNITS_VERSION = "2026.11.25"
