"""Embedded text carries where a block lives, not only what it contains.

Measured on a live index for the query `"stream record" implementation`: the
top eight results were six test files and two TypeScript files, and
`connector_service.py` -- which defines the abstract `stream_record` contract --
was returned by neither of the two searches at all. Its blocks are a signature
and a docstring, so there was almost nothing in them to match on; the words
that identify the file live in its name and path.
"""
from types import SimpleNamespace

from app.modules.transformers.vectorstore import (
    VectorStore,
    _build_code_documents,
    _record_context_line,
)

_build_summary = VectorStore._build_record_summary_document

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
        """`/org/repo/-/blob/HEAD/` is identical for every file in the repo and
        so separates nothing, while diluting the terms that do."""
        line = _record_context_line(
            _record("factory.py", "/acme/repo/-/blob/HEAD/app/services/vector_db/factory.py")
        )
        assert line == "factory.py — app/services/vector_db/factory.py"

    def test_collapses_when_name_equals_path(self) -> None:
        assert _record_context_line(_record("README.md", "README.md")) == "README.md"

    def test_no_record_yields_no_prefix(self) -> None:
        assert _record_context_line(None) == ""


class TestCodeDocuments:
    def test_every_block_carries_path_and_its_own_symbol(self) -> None:
        """Per block, not once per record -- the block is the retrieval unit."""
        ctx = _record_context_line(
            _record("connector_service.py",
                    "/acme/repo/-/blob/HEAD/app/connectors/core/base/connector/connector_service.py")
        )
        docs = _build_code_documents(
            [
                _code_block("def stream_record(self, record): ...",
                            "method:BaseConnector.stream_record", "b1", 0),
                _code_block("def run_sync(self): ...",
                            "method:BaseConnector.run_sync", "b2", 1),
            ],
            VRID, ORG, ctx,
        )
        assert len(docs) == 2
        for doc in docs:
            assert "app/connectors/core/base/connector/connector_service.py" in doc.page_content
        assert "method:BaseConnector.stream_record" in docs[0].page_content
        assert "method:BaseConnector.run_sync" in docs[1].page_content
        assert "method:BaseConnector.run_sync" not in docs[0].page_content

    def test_an_abstract_stub_gains_the_terms_it_lacked(self) -> None:
        """The failing case: a body with nothing in it to match on."""
        ctx = _record_context_line(_record("connector_service.py", "app/.../connector_service.py"))
        doc = _build_code_documents(
            [_code_block("    ...", "method:BaseConnector.stream_record")], VRID, ORG, ctx
        )[0]
        assert "stream_record" in doc.page_content

    def test_source_text_survives(self) -> None:
        doc = _build_code_documents(
            [_code_block("x = 1", "function:f")], VRID, ORG, "a.py — a.py"
        )[0]
        assert "x = 1" in doc.page_content

    def test_unchanged_without_context_or_symbol(self) -> None:
        assert _build_code_documents([_code_block("x = 1")], VRID, ORG)[0].page_content == "x = 1"

    def test_empty_blocks_are_still_skipped(self) -> None:
        assert _build_code_documents([_code_block("   ")], VRID, ORG, "ctx") == []


class TestRecordSummaryDocument:
    _SUMMARY = SimpleNamespace(
        summary="Synchronizes Jira projects, issues and permissions."
    )

    def test_every_record_gets_one(self) -> None:
        doc = _build_summary("rec-1", VRID, ORG, self._SUMMARY)
        assert doc is not None
        assert doc.metadata["isRecordSummary"] is True

    def test_still_none_without_a_summary(self) -> None:
        assert _build_summary("rec-1", VRID, ORG, SimpleNamespace(summary="")) is None
