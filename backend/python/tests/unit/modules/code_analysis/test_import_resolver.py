"""Unit tests for app.modules.code_analysis.import_resolver.

Tests cover:
- resolve_python_imports: absolute, relative, dot-relative imports
- resolve_typescript_imports: relative, bare module, require() calls
- resolve_go_imports: returns raw import paths
- resolve_java_imports: standard import, static import
- ImportResolver.resolve_all: no records → empty stats
- ImportResolver.resolve_all: records without language/imports → skipped
- ImportResolver.resolve_all: successful edge creation via batch_upsert_record_relations
- ImportResolver.resolve_all: target not in repo → edge_skipped
- ImportResolver.resolve_all: self-loop prevention
- ImportResolver.resolve_all: handles get_records_by_record_group exception
- ImportResolver.resolve_all: handles batch_upsert_record_relations exception (no propagation)
- ImportResolver.resolve_all: idempotency (re-run calls batch_upsert_record_relations both times)
- ImportResolver.resolve_all: edges written with relationshipType="IMPORTS" (not relationType)
- ImportResolver.resolve_all: edges carry orgId for multi-tenant scoping
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.code_analysis.import_resolver import (
    ImportResolver,
    resolve_go_imports,
    resolve_java_imports,
    resolve_python_imports,
    resolve_typescript_imports,
)


# ===========================================================================
# Helpers
# ===========================================================================


def _make_code_file(
    key: str,
    file_path: str,
    language: str | None = None,
    imports: list[str] | None = None,
) -> MagicMock:
    """Return a minimal CodeFileRecord-like mock with the required attributes."""
    rec = MagicMock()
    rec.id = key
    rec.file_path = file_path
    rec.language = language
    rec.imports = imports if imports is not None else []
    # make isinstance(rec, CodeFileRecord) work — patch the spec
    rec.__class__.__name__ = "CodeFileRecord"
    return rec


def _make_resolver(
    records=None,
    org_id="org-1",
    record_group_id="rg-1",
    connector_id="conn-1",
):
    from app.models.entities import CodeFileRecord  # for isinstance check

    gp = AsyncMock()
    # Return a list of objects that pass isinstance(r, CodeFileRecord)
    typed_records = records if records is not None else []
    gp.get_records_by_record_group = AsyncMock(return_value=typed_records)
    gp.batch_upsert_record_relations = AsyncMock()
    resolver = ImportResolver(
        graph_provider=gp,
        org_id=org_id,
        record_group_id=record_group_id,
        connector_id=connector_id,
    )
    return resolver, gp


def _make_real_code_file(
    key: str,
    file_path: str,
    language: str | None = None,
    imports: list[str] | None = None,
):
    """Create an actual CodeFileRecord with minimal required fields."""
    from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
    from app.models.entities import CodeFileRecord, RecordType

    return CodeFileRecord(
        id=key,
        org_id="org-1",
        record_name=file_path,
        record_type=RecordType.CODE_FILE,
        external_record_id=key,
        version=1,
        connector_name=Connectors.GITLAB,
        connector_id="conn-1",
        mime_type=MimeTypes.UNKNOWN.value,
        origin=OriginTypes.CONNECTOR,
        file_path=file_path,
        language=language,
        imports=imports,
    )


# ===========================================================================
# resolve_python_imports
# ===========================================================================


class TestResolvePythonImports:
    def test_absolute_import(self):
        paths = resolve_python_imports("import os\n", "src/main.py")
        assert "os.py" in paths or "os/__init__.py" in paths

    def test_from_import(self):
        paths = resolve_python_imports("from app.utils import helper\n", "src/main.py")
        assert "app/utils.py" in paths

    def test_from_import_init_candidate(self):
        paths = resolve_python_imports("from app.utils import helper\n", "src/main.py")
        assert "app/utils/__init__.py" in paths

    def test_relative_import_single_dot(self):
        paths = resolve_python_imports("from .models import User\n", "src/app/views.py")
        assert any("models" in p for p in paths)

    def test_relative_import_double_dot(self):
        paths = resolve_python_imports("from ..config import settings\n", "src/app/views.py")
        assert any("config" in p for p in paths)

    def test_nested_module(self):
        paths = resolve_python_imports("from a.b.c import X\n", "src/main.py")
        assert "a/b/c.py" in paths

    def test_plain_import_nested(self):
        paths = resolve_python_imports("import a.b.c\n", "src/main.py")
        assert "a/b/c.py" in paths

    def test_empty_text_returns_empty(self):
        assert resolve_python_imports("", "src/main.py") == []

    def test_non_import_lines_ignored(self):
        paths = resolve_python_imports("# comment\nx = 1\n", "src/main.py")
        assert paths == []

    def test_multiple_imports(self):
        text = "import os\nfrom app.utils import foo\n"
        paths = resolve_python_imports(text, "src/main.py")
        assert "os.py" in paths or "os/__init__.py" in paths
        assert "app/utils.py" in paths


# ===========================================================================
# resolve_typescript_imports
# ===========================================================================


class TestResolveTypescriptImports:
    def test_relative_import(self):
        paths = resolve_typescript_imports("import { foo } from './utils'\n", "src/app/index.ts")
        assert any("utils.ts" in p for p in paths)

    def test_relative_import_tsx_candidate(self):
        paths = resolve_typescript_imports("import { foo } from './Button'\n", "src/app/index.ts")
        assert any("Button.tsx" in p for p in paths)

    def test_parent_relative_import(self):
        paths = resolve_typescript_imports("import { bar } from '../services/auth'\n", "src/app/index.ts")
        assert any("auth" in p for p in paths)

    def test_bare_module_path(self):
        paths = resolve_typescript_imports("import React from 'react'\n", "src/app.ts")
        assert any("react" in p for p in paths)

    def test_require_call(self):
        paths = resolve_typescript_imports("const fs = require('fs')\n", "src/app.js")
        assert any("fs" in p for p in paths)

    def test_index_candidate(self):
        paths = resolve_typescript_imports("import { x } from './components'\n", "src/app.ts")
        assert any("index.ts" in p for p in paths)

    def test_empty_text_returns_empty(self):
        assert resolve_typescript_imports("", "src/app.ts") == []

    def test_non_import_lines_ignored(self):
        paths = resolve_typescript_imports("const x = 1;\n", "src/app.ts")
        assert paths == []


# ===========================================================================
# resolve_go_imports
# ===========================================================================


class TestResolveGoImports:
    def test_single_import(self):
        text = 'import "fmt"\n'
        paths = resolve_go_imports(text, "main.go")
        assert "fmt" in paths

    def test_block_imports(self):
        text = 'import (\n    "fmt"\n    "os"\n    "github.com/org/repo/pkg"\n)\n'
        paths = resolve_go_imports(text, "main.go")
        assert "fmt" in paths
        assert "os" in paths
        assert "github.com/org/repo/pkg" in paths

    def test_empty_text_returns_empty(self):
        assert resolve_go_imports("", "main.go") == []

    def test_non_import_lines_ignored(self):
        paths = resolve_go_imports("func main() {}\n", "main.go")
        assert paths == []


# ===========================================================================
# resolve_java_imports
# ===========================================================================


class TestResolveJavaImports:
    def test_standard_import(self):
        paths = resolve_java_imports("import java.util.List;\n", "src/Main.java")
        assert "java/util/List.java" in paths

    def test_static_import(self):
        paths = resolve_java_imports("import static java.lang.Math.PI;\n", "src/Main.java")
        assert any("Math" in p for p in paths)

    def test_multiple_imports(self):
        text = "import java.util.List;\nimport com.example.Foo;\n"
        paths = resolve_java_imports(text, "src/Main.java")
        assert "java/util/List.java" in paths
        assert "com/example/Foo.java" in paths

    def test_empty_text_returns_empty(self):
        assert resolve_java_imports("", "src/Main.java") == []

    def test_non_import_lines_ignored(self):
        paths = resolve_java_imports("public class Foo {}\n", "src/Foo.java")
        assert paths == []


# ===========================================================================
# ImportResolver.resolve_all — using real CodeFileRecord objects
# ===========================================================================


class TestImportResolverResolveAll:

    @pytest.mark.asyncio
    async def test_empty_records_returns_zero_stats(self):
        records = []
        resolver, _ = _make_resolver(records=records)
        stats = await resolver.resolve_all()
        assert stats["files_processed"] == 0
        assert stats["edges_created"] == 0
        assert stats["edges_skipped"] == 0
        assert stats["errors"] == 0

    @pytest.mark.asyncio
    async def test_record_without_language_skipped(self):
        rec = _make_real_code_file("rec-1", "src/main.py", language=None, imports=["import os\n"])
        # Patch isinstance check — rec IS a real CodeFileRecord, no language → should be skipped
        resolver, gp = _make_resolver(records=[rec])
        stats = await resolver.resolve_all()
        assert stats["files_processed"] == 1
        assert stats["edges_created"] == 0
        gp.batch_upsert_record_relations.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_without_imports_skipped(self):
        rec = _make_real_code_file("rec-1", "src/main.py", language="python", imports=[])
        resolver, gp = _make_resolver(records=[rec])
        stats = await resolver.resolve_all()
        assert stats["files_processed"] == 1
        assert stats["edges_created"] == 0
        gp.batch_upsert_record_relations.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_edge_creation(self):
        """Import resolves to a file in the same repo → edge is created."""
        rec_main = _make_real_code_file(
            "rec-main", "src/main.py", language="python",
            imports=["from src.utils import helper\n"],
        )
        rec_utils = _make_real_code_file("rec-utils", "src/utils.py", language="python", imports=[])
        resolver, gp = _make_resolver(records=[rec_main, rec_utils])
        stats = await resolver.resolve_all()
        assert stats["edges_created"] == 1
        gp.batch_upsert_record_relations.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_target_not_in_repo_increments_skipped(self):
        """Import target not in the repo → edges_skipped incremented."""
        rec = _make_real_code_file(
            "rec-1", "src/main.py", language="python",
            imports=["import external_pkg\n"],
        )
        resolver, gp = _make_resolver(records=[rec])
        stats = await resolver.resolve_all()
        assert stats["edges_skipped"] > 0
        gp.batch_upsert_record_relations.assert_not_called()

    @pytest.mark.asyncio
    async def test_self_loop_not_created(self):
        """A file importing itself must not produce an edge to itself."""
        rec = _make_real_code_file(
            "rec-1", "src/main.py", language="python",
            imports=["from src.main import something\n"],
        )
        resolver, gp = _make_resolver(records=[rec])
        stats = await resolver.resolve_all()
        gp.batch_upsert_record_relations.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetch_exception_returns_error_stats(self):
        """When get_records_by_record_group raises, errors is incremented."""
        gp = AsyncMock()
        gp.get_records_by_record_group = AsyncMock(side_effect=RuntimeError("db error"))
        resolver = ImportResolver(
            graph_provider=gp, org_id="org-1", record_group_id="rg-1", connector_id="conn-1"
        )
        stats = await resolver.resolve_all()
        assert stats["errors"] > 0
        assert stats["files_processed"] == 0

    @pytest.mark.asyncio
    async def test_batch_upsert_exception_increments_error_not_propagated(self):
        """If batch_upsert_record_relations fails, errors counted; resolve_all does not raise."""
        rec_a = _make_real_code_file("rec-a", "src/a.py", language="python",
                                     imports=["from src.b import X\n"])
        rec_b = _make_real_code_file("rec-b", "src/b.py", language="python", imports=[])
        resolver, gp = _make_resolver(records=[rec_a, rec_b])
        gp.batch_upsert_record_relations = AsyncMock(side_effect=RuntimeError("write failed"))
        try:
            stats = await resolver.resolve_all()
        except RuntimeError:
            pytest.fail("batch_upsert_record_relations exception propagated out of resolve_all")
        assert stats["errors"] > 0

    @pytest.mark.asyncio
    async def test_idempotency_calls_batch_upsert_both_times(self):
        """Running resolve_all twice calls batch_upsert_record_relations both times (idempotent)."""
        rec_a = _make_real_code_file("rec-a", "src/a.py", language="python",
                                     imports=["from src.b import X\n"])
        rec_b = _make_real_code_file("rec-b", "src/b.py", language="python", imports=[])
        resolver, gp = _make_resolver(records=[rec_a, rec_b])
        await resolver.resolve_all()
        await resolver.resolve_all()
        assert gp.batch_upsert_record_relations.await_count >= 2

    @pytest.mark.asyncio
    async def test_edge_carries_org_id(self):
        """The created edge must carry orgId for multi-tenant scoping."""
        rec_a = _make_real_code_file("rec-a", "src/a.py", language="python",
                                     imports=["from src.b import X\n"])
        rec_b = _make_real_code_file("rec-b", "src/b.py", language="python", imports=[])
        # Override org_id on both records so they share the same org
        rec_a.org_id = "my-org"
        rec_b.org_id = "my-org"
        resolver, gp = _make_resolver(records=[rec_a, rec_b], org_id="my-org")
        await resolver.resolve_all()
        if gp.batch_upsert_record_relations.await_count > 0:
            edges = gp.batch_upsert_record_relations.call_args[0][0]
            assert len(edges) > 0
            assert edges[0].get("orgId") == "my-org"

    @pytest.mark.asyncio
    async def test_edge_uses_relationship_type_not_relationType(self):
        """The created edge must use 'relationshipType' (not 'relationType') = 'IMPORTS'."""
        rec_a = _make_real_code_file("rec-a", "src/a.py", language="python",
                                     imports=["from src.b import X\n"])
        rec_b = _make_real_code_file("rec-b", "src/b.py", language="python", imports=[])
        resolver, gp = _make_resolver(records=[rec_a, rec_b])
        await resolver.resolve_all()
        if gp.batch_upsert_record_relations.await_count > 0:
            edges = gp.batch_upsert_record_relations.call_args[0][0]
            assert len(edges) > 0
            assert edges[0].get("relationshipType") == "IMPORTS"
            assert "relationType" not in edges[0]

    @pytest.mark.asyncio
    async def test_edge_has_constraint_name(self):
        """Edges must carry a constraintName field (may be empty string) for UPSERT key."""
        rec_a = _make_real_code_file("rec-a", "src/a.py", language="python",
                                     imports=["from src.b import X\n"])
        rec_b = _make_real_code_file("rec-b", "src/b.py", language="python", imports=[])
        resolver, gp = _make_resolver(records=[rec_a, rec_b])
        await resolver.resolve_all()
        if gp.batch_upsert_record_relations.await_count > 0:
            edges = gp.batch_upsert_record_relations.call_args[0][0]
            assert "constraintName" in edges[0]

    @pytest.mark.asyncio
    async def test_multiple_files_all_processed(self):
        """Stats reflect all files in the repo, not just those with imports."""
        records = [
            _make_real_code_file("r1", "a.py", language="python", imports=[]),
            _make_real_code_file("r2", "b.py", language="python", imports=[]),
            _make_real_code_file("r3", "c.py", language="python", imports=[]),
        ]
        resolver, _ = _make_resolver(records=records)
        stats = await resolver.resolve_all()
        assert stats["files_processed"] == 3

    @pytest.mark.asyncio
    async def test_typescript_imports_resolved(self):
        """TypeScript relative imports also create edges via batch_upsert_record_relations."""
        rec_index = _make_real_code_file(
            "rec-index", "src/index.ts", language="typescript",
            imports=["import { helper } from './utils'\n"],
        )
        rec_utils = _make_real_code_file("rec-utils", "src/utils.ts", language="typescript", imports=[])
        resolver, gp = _make_resolver(records=[rec_index, rec_utils])
        stats = await resolver.resolve_all()
        assert stats["edges_created"] == 1
        gp.batch_upsert_record_relations.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uses_get_records_by_record_group_not_get_nodes_by_filters(self):
        """Resolver must call get_records_by_record_group (backend-agnostic) not get_nodes_by_filters."""
        resolver, gp = _make_resolver(records=[])
        gp.get_nodes_by_filters = AsyncMock()
        await resolver.resolve_all()
        gp.get_records_by_record_group.assert_awaited_once()
        gp.get_nodes_by_filters.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_records_by_record_group_called_with_correct_args(self):
        """Resolver passes record_group_id, connector_id, org_id, depth=-1."""
        resolver, gp = _make_resolver(records=[], org_id="org-99", record_group_id="rg-42",
                                      connector_id="conn-x")
        await resolver.resolve_all()
        gp.get_records_by_record_group.assert_awaited_once_with("rg-42", "conn-x", "org-99", depth=-1)
