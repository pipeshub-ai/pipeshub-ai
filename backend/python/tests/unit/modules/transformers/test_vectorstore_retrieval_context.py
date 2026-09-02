"""Embedded text carries where a block lives, and tests get no summary vector.

Measured on a live index for the query `"stream record" implementation`: the
top eight results were six test files and two TypeScript files, and
`connector_service.py` -- which defines the abstract `stream_record` contract --
was returned by neither of the two searches at all.

Two causes, one per change here. The abstract method is a signature and a
docstring, so its block had almost no text to match on; and every test file
carried a summary vector describing the thing it tests, in that thing's own
words.
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


def _record(name, external_id, file_role=None):
    return SimpleNamespace(
        record_name=name, external_record_id=external_id, file_role=file_role
    )


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

    def test_a_test_file_gets_no_summary_vector(self) -> None:
        """Its summary describes the connector, not the test, so it competes
        with the connector for the connector's own queries."""
        assert _build_summary(
            "rec-1", VRID, ORG, self._SUMMARY,
            _record("test_jira_connector.py", "tests/test_jira_connector.py", file_role="test"),
        ) is None

    def test_role_match_is_case_insensitive(self) -> None:
        assert _build_summary(
            "rec-1", VRID, ORG, self._SUMMARY,
            _record("t.py", "t.py", file_role="TEST"),
        ) is None

    def test_source_files_still_get_one(self) -> None:
        doc = _build_summary(
            "rec-1", VRID, ORG, self._SUMMARY,
            _record("jira_connector.py", "app/jira_connector.py", file_role="source"),
        )
        assert doc is not None
        assert doc.metadata["isRecordSummary"] is True

    def test_records_with_no_role_are_unaffected(self) -> None:
        """Non-code records (documents, mail) carry no file_role at all."""
        assert _build_summary("rec-1", VRID, ORG, self._SUMMARY, _record("a.pdf", "a.pdf")) is not None

    def test_record_is_optional(self) -> None:
        assert _build_summary("rec-1", VRID, ORG, self._SUMMARY) is not None

    def test_still_none_without_a_summary(self) -> None:
        assert _build_summary(
            "rec-1", VRID, ORG, SimpleNamespace(summary=""), _record("a.py", "a.py")
        ) is None
