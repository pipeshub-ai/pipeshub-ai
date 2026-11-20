"""The parser identity: one derivation for the event payload and for the graph, pinned to a table of real spellings.

A routing change (a new type, a widened spelling) changes the identity of every record of that type, which makes each
same-bytes update of them run in full once. This table makes that a reviewed decision, like a bump of ``PARSER_ROUTE_REV``.
"""
from __future__ import annotations

import pytest

from app.modules.pipeline import parser_identity as pi
from tests.support.parser_identity_world import (
    corpus,
    file_record,
    graph_of,
    payload_of,
)

GOLDEN = [
    ("text/plain", "txt", "a.txt", "FILE", "txt@1"),
    ("text/markdown", "md", "a.md", "FILE", "md@1"),
    ("text/plain", "md", "a.md", "FILE", "txt@1"),
    ("application/octet-stream", "md", "a.md", "FILE", "md@1"),
    ("application/octet-stream", "txt", "a", "FILE", "txt@1"),
    ("application/octet-stream", None, "a", "FILE", None),
    ("text/html", "html", "a.html", "FILE", "html@1"),
    ("text/html", "htm", "a.htm", "FILE", "html@1"),
    ("application/octet-stream", "htm", "a.htm", "FILE", "html@1"),
    ("application/pdf", "pdf", "a.pdf", "FILE", "pdf@1"),
    ("text/csv", "csv", "a.csv", "FILE", "csv@1"),
    ("application/json", "json", "a.json", "FILE", "json@1"),
    ("application/x-yaml", "yml", "a.yml", "FILE", "yaml@1"),
    ("text/plain; charset=utf-8", "txt", "a.txt", "FILE", "txt@1"),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx", "a.docx", "FILE", "docx@1"),
    ("application/vnd.google-apps.document", None, "d", "FILE", "google_docs@1"),
    ("text/plain", None, "subject", "MAIL", "txt@1"),
    # not tracked: code, repository files, unknown
    ("text/plain", "py", "a.py", "FILE", None),
    ("text/x-python", "py", "a.py", "FILE", None),
    ("text/plain", None, "a.txt", "CODE_FILE", None),
    ("unknown", "unknown", "a", "FILE", None),
]


@pytest.mark.parametrize(("mime", "ext", "name", "record_type", "expected"), GOLDEN)
def test_the_route_map_is_pinned(mime: str, ext: str | None, name: str, record_type: str, expected: str | None) -> None:
    assert pi.parser_key(mime, ext, name, record_type) == expected


@pytest.mark.parametrize("pipeline", [pi.PIPELINE_IN_PROCESS, pi.PIPELINE_SERVICE])
def test_the_identity_is_the_pipeline_and_the_key(pipeline: str) -> None:
    assert pi.parser_identity("text/plain", "txt", "a.txt", "FILE", pipeline=pipeline) == f"{pipeline}:txt@1"
    assert pi.parser_identity("text/plain", "py", "a.py", "FILE", pipeline=pipeline) is None


def test_the_pipeline_is_not_part_of_what_is_compared() -> None:
    assert pi.key_of("inproc:txt@1") == pi.key_of("svc:txt@1") == "txt@1"
    assert pi.key_of(None) is None
    assert pi.key_of("garbage") is None


def test_a_revision_bump_changes_the_key_of_its_route_only(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config.file_types import Route

    monkeypatch.setitem(pi.PARSER_ROUTE_REV, Route.TXT, 2)
    assert pi.parser_key("text/plain", "txt", "a.txt", "FILE") == "txt@2"
    assert pi.parser_key("text/markdown", "md", "a.md", "FILE") == "md@1"


@pytest.mark.parametrize(("name", "extension", "mime"), corpus())
def test_the_payload_and_the_graph_give_the_same_identity(name: str, extension: str | None, mime: str) -> None:
    """The graph's ``records`` node has no extension; the identity is read from it with the ``files`` document.

    Both sides are real: the payload is ``to_kafka_record`` and the graph side is what ``to_arango_base_record`` and
    ``to_arango_record`` store.
    """
    record = file_record(name, extension, mime)
    payload = payload_of(record)
    node, file_doc = graph_of(record)
    assert "extension" not in node
    from_payload = pi.parser_identity(
        payload["mimeType"], payload["extension"], payload["recordName"], payload["recordType"], pipeline="inproc",
    )
    from_graph = pi.graph_parser_key(node, file_doc)
    assert pi.key_of(from_payload) == from_graph


def test_a_graph_read_without_the_files_document_differs_from_the_payload_for_octet_stream() -> None:
    """The negative control of the parity test: reading the records node alone (no files document) loses the type."""
    record = file_record("notes.txt", "txt", "application/octet-stream")
    node, _ = graph_of(record)
    assert pi.graph_parser_key(node, None) != pi.key_of(
        pi.parser_identity("application/octet-stream", "txt", "notes.txt", "FILE", pipeline="inproc")
    )


def test_extensionless_and_unknown_spellings_are_one_input() -> None:
    assert pi.parser_key("text/plain", None, "a.txt", "FILE") == pi.parser_key("text/plain", "unknown", "a.txt", "FILE")
    assert pi.parser_key("text/plain", "", "a.txt", "FILE") == pi.parser_key("text/plain", None, "a.txt", "FILE")
