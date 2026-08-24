"""Embedded text carries the record's name and path, not only its content.

Measured on a live index: a query naming a module ("vector database backends
runtime selection") ranked the file that implements it 25th of 31, below three
test files, three TypeScript files and four READMEs. Its own blocks matched on
nothing, because the words that identify it live in its name and path and
neither appeared in any embedded text. The extracted summary does not help --
it describes what a file is ABOUT, so a connector's tests and the connector
itself read almost identically.
"""
from types import SimpleNamespace

from app.modules.transformers.vectorstore import (
    VectorStore,
    _build_code_documents,
    _record_context_line,
)

_build_record_summary_document = VectorStore._build_record_summary_document

VRID = "vrid-1"
ORG = "org-1"


def _record(name, external_id):
    return SimpleNamespace(record_name=name, external_record_id=external_id)


def _code_block(text, qualified_name=None, block_id="b1", index=0):
    return SimpleNamespace(
        id=block_id,
        index=index,
        data={"text": text},
        code_metadata=SimpleNamespace(qualified_name=qualified_name),
    )


class TestContextLine:
    def test_strips_repo_hosting_prefix(self) -> None:
        """`/org/repo/-/blob/HEAD/` is identical for every file in the repo, so
        it separates nothing and only dilutes the terms that do."""
        line = _record_context_line(
            _record("factory.py", "/acme/repo/-/blob/HEAD/app/services/vector_db/factory.py")
        )
        assert line == "factory.py — app/services/vector_db/factory.py"
        assert "blob/HEAD" not in line

    def test_collapses_when_name_equals_path(self) -> None:
        line = _record_context_line(_record("README.md", "README.md"))
        assert line == "README.md"

    def test_no_record_yields_no_prefix(self) -> None:
        assert _record_context_line(None) == ""


class TestCodeDocuments:
    def test_block_carries_path_and_qualified_name(self) -> None:
        ctx = _record_context_line(
            _record("factory.py", "/acme/repo/-/blob/HEAD/app/services/vector_db/factory.py")
        )
        docs = _build_code_documents(
            [_code_block("async def create_provider(): ...", "function:create_provider")],
            VRID, ORG, ctx,
        )
        content = docs[0].page_content
        assert "app/services/vector_db/factory.py" in content
        assert "function:create_provider" in content
        assert "async def create_provider" in content, "source text must survive"

    def test_source_is_unchanged_without_context(self) -> None:
        docs = _build_code_documents([_code_block("x = 1")], VRID, ORG)
        assert docs[0].page_content == "x = 1"

    def test_empty_blocks_are_still_skipped(self) -> None:
        assert _build_code_documents([_code_block("   ")], VRID, ORG, "ctx") == []


class TestRecordSummaryDocument:
    def test_none_without_a_summary(self) -> None:
        """Emitting a document here would create a summary vector for every
        record that never had extraction run."""
        assert _build_record_summary_document(
            "rec-1", VRID, ORG, SimpleNamespace(summary="")
        ) is None

    def test_page_content_is_the_summary(self) -> None:
        doc = _build_record_summary_document(
            "rec-1", VRID, ORG, SimpleNamespace(summary="A summary.")
        )
        assert doc.page_content == "A summary."
