"""
Import resolver: resolves import statements on CODE_FILE records into
``IMPORTS`` edges in the ``recordRelations`` collection.

Runs as a **post-sync batch job** after a full repository sync completes.
It is fully backend-agnostic — uses only ``IGraphDBProvider`` methods that
are implemented by both ArangoDB and Neo4j providers.

Design principles:
- Backend-agnostic: uses ``get_records_by_record_group`` (returns typed
  ``CodeFileRecord`` objects on both backends) and
  ``batch_upsert_record_relations`` (UPSERT on _from/_to/relationshipType/
  constraintName, so it is safe to re-run).
- Scoped by record group and orgId to prevent cross-tenant/cross-repo edges.
- Idempotent: re-running on the same repo re-creates edges from scratch (UPSERT).
- Language-specific resolvers are standalone functions; new languages are added
  without touching the orchestrator.

Usage (called from the repo-sync-complete hook):

    resolver = ImportResolver(
        graph_provider=gp,
        org_id="...",
        record_group_id="...",
        connector_id="...",
    )
    stats = await resolver.resolve_all()
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from app.config.constants.arangodb import CollectionNames, RecordRelations
from app.models.entities import CodeFileRecord
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language-specific import path normalisation
# ---------------------------------------------------------------------------


def resolve_python_imports(import_text: str, file_path: str) -> list[str]:
    """Parse Python import statements and return candidate file paths.

    Resolves both ``import a.b.c`` and ``from a.b.c import X`` patterns to
    candidate relative file paths (``a/b/c.py`` and ``a/b/c/__init__.py``).

    Args:
        import_text: The raw text of the imports block.
        file_path: Repo-relative path of the file containing the imports
                   (used only for relative import resolution).

    Returns:
        List of candidate repo-relative file paths (without leading ``/``).
    """
    candidates: list[str] = []
    file_dir = os.path.dirname(file_path)

    for line in import_text.splitlines():
        line = line.strip()
        # ``from .utils import X`` or ``from ..models import Y``
        m = re.match(r"^from\s+(\.+)([\w.]*)\s+import", line)
        if m:
            dots = m.group(1)
            module = m.group(2)
            base = file_dir
            for _ in dots[1:]:  # each extra dot goes one level up
                base = os.path.dirname(base)
            rel = module.replace(".", "/")
            prefix = os.path.join(base, rel) if rel else base
            candidates.extend([f"{prefix}.py", f"{prefix}/__init__.py"])
            continue

        # ``from a.b.c import X``
        m = re.match(r"^from\s+([\w.]+)\s+import", line)
        if m:
            module_path = m.group(1).replace(".", "/")
            candidates.extend([f"{module_path}.py", f"{module_path}/__init__.py"])
            continue

        # ``import a.b.c``
        m = re.match(r"^import\s+([\w.]+)", line)
        if m:
            module_path = m.group(1).replace(".", "/")
            candidates.extend([f"{module_path}.py", f"{module_path}/__init__.py"])

    return candidates


def resolve_typescript_imports(import_text: str, file_path: str) -> list[str]:
    """Parse TypeScript/JavaScript import statements.

    Resolves ``import ... from './utils'`` style paths to candidate file paths.
    Handles relative paths (``./``, ``../``) and bare module paths.
    """
    candidates: list[str] = []
    file_dir = os.path.dirname(file_path)

    for line in import_text.splitlines():
        m = re.search(r"""from\s+['"]([^'"]+)['"]""", line)
        if not m:
            m = re.search(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""", line)
        if not m:
            continue
        path = m.group(1)
        if path.startswith("."):
            base = os.path.normpath(os.path.join(file_dir, path))
        else:
            base = path
        # TypeScript resolution order: exact, .ts, .tsx, .js, /index.ts
        candidates.extend([
            f"{base}.ts", f"{base}.tsx", f"{base}.js",
            f"{base}/index.ts", f"{base}/index.tsx", f"{base}/index.js",
        ])

    return candidates


def resolve_go_imports(import_text: str, _file_path: str) -> list[str]:
    """Parse Go import declarations.

    Returns the raw import paths (``github.com/org/repo/pkg``) since Go module
    paths cannot be resolved to file paths without the full module cache.
    The caller stores these as symbolic ``sourceSymbol`` on the edge.
    """
    paths: list[str] = []
    for line in import_text.splitlines():
        m = re.search(r'"([^"]+)"', line)
        if m:
            paths.append(m.group(1))
    return paths


def resolve_java_imports(import_text: str, _file_path: str) -> list[str]:
    """Parse Java import statements (``import com.foo.Bar;``)."""
    paths: list[str] = []
    for line in import_text.splitlines():
        m = re.match(r"import\s+(?:static\s+)?([\w.]+);", line.strip())
        if m:
            paths.append(m.group(1).replace(".", "/") + ".java")
    return paths


_RESOLVERS: dict[str, Any] = {
    "python": resolve_python_imports,
    "typescript": resolve_typescript_imports,
    "tsx": resolve_typescript_imports,
    "javascript": resolve_typescript_imports,
    "go": resolve_go_imports,
    "java": resolve_java_imports,
}


# ---------------------------------------------------------------------------
# ImportResolver
# ---------------------------------------------------------------------------


class ImportResolver:
    """Resolve import statements in a repository to ``IMPORTS`` graph edges.

    Args:
        graph_provider: Graph DB provider (ArangoDB or Neo4j) — only uses
            methods declared on ``IGraphDBProvider``.
        org_id: Organisation ID for multi-tenant scoping.
        record_group_id: Internal ``RecordGroup._key`` / ``id`` of the
            repository being analysed.
        connector_id: Connector ID associated with the repository (required
            by ``get_records_by_record_group``).
    """

    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        org_id: str,
        record_group_id: str,
        connector_id: str,
    ) -> None:
        self._gp = graph_provider
        self._org_id = org_id
        self._record_group_id = record_group_id
        self._connector_id = connector_id
        self._logger = logging.getLogger(f"{__name__}.{record_group_id}")

    async def resolve_all(self) -> dict[str, int]:
        """Resolve all imports in the repository and persist IMPORTS edges.

        Returns:
            Stats dict with ``files_processed``, ``edges_created``,
            ``edges_skipped`` (target not found in repo), ``errors``.
        """
        stats = {"files_processed": 0, "edges_created": 0, "edges_skipped": 0, "errors": 0}

        # Fetch all records in this record group (returns typed Record objects
        # on both ArangoDB and Neo4j backends).
        try:
            all_records = await self._gp.get_records_by_record_group(
                self._record_group_id,
                self._connector_id,
                self._org_id,
                depth=-1,
            )
        except Exception as exc:
            self._logger.error("Failed to fetch records for group %s: %s", self._record_group_id, exc)
            stats["errors"] += 1
            return stats

        # Filter to CODE_FILE records only (record group may include other types
        # such as TICKET / PULL_REQUEST in a GitLab project).
        code_files: list[CodeFileRecord] = [
            r for r in all_records if isinstance(r, CodeFileRecord)
        ]

        if not code_files:
            self._logger.info(
                "No CODE_FILE records found in record group %s", self._record_group_id
            )
            return stats

        # Build lookup: repo-relative file_path → record_id
        path_to_record: dict[str, str] = {}
        for rec in code_files:
            fp = rec.file_path or ""
            if fp and rec.id:
                path_to_record[fp.lstrip("/")] = rec.id

        self._logger.info(
            "Import resolution: %d CODE_FILE records in group %s",
            len(code_files),
            self._record_group_id,
        )

        for rec in code_files:
            try:
                await self._resolve_record_imports(rec, path_to_record, stats)
                stats["files_processed"] += 1
            except Exception as exc:
                self._logger.warning("Error resolving imports for %s: %s", rec.id, exc)
                stats["errors"] += 1

        self._logger.info("Import resolution complete: %s", stats)
        return stats

    async def _resolve_record_imports(
        self,
        rec: CodeFileRecord,
        path_to_record: dict[str, str],
        stats: dict[str, int],
    ) -> None:
        record_id = rec.id or ""
        language = rec.language or ""
        imports_list: list[str] = rec.imports or []
        file_path: str = rec.file_path or ""

        if not language or not imports_list or not file_path:
            return

        resolver_fn = _RESOLVERS.get(language)
        if resolver_fn is None:
            return

        edges: list[dict] = []
        for import_text in imports_list:
            candidates = resolver_fn(import_text, file_path)
            for candidate in candidates:
                normalized = candidate.lstrip("/")
                target_record_id = path_to_record.get(normalized)
                if target_record_id is None:
                    stats["edges_skipped"] += 1
                    continue
                if target_record_id == record_id:
                    continue  # skip self-loops
                edges.append({
                    "_from": f"{CollectionNames.RECORDS.value}/{record_id}",
                    "_to": f"{CollectionNames.RECORDS.value}/{target_record_id}",
                    "relationshipType": RecordRelations.IMPORTS.value,
                    "constraintName": "",
                    "orgId": self._org_id,
                    "sourceSymbol": import_text[:200],
                    "targetSymbol": os.path.basename(normalized),
                })
                break  # first matching candidate wins; move to next import

        if not edges:
            return

        try:
            await self._gp.batch_upsert_record_relations(edges)
            stats["edges_created"] += len(edges)
        except Exception as exc:
            self._logger.warning(
                "Failed to upsert IMPORTS edges for %s: %s", record_id, exc
            )
            stats["errors"] += len(edges)
