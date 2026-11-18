"""``content_tag`` v2: the validator of a record's served bytes."""

from __future__ import annotations

import pytest

from app.services.storage_documents import StorageDocumentInfo, parse_document_info
from app.utils.content_tag import content_tag, if_none_match_matches

PIPELINE = "lo:LibreOffice 7.6|conv:1"


def _info(*, doc="doc-1", fp="fp-1", trusted=True) -> StorageDocumentInfo:
    body = {
        "document": {"_id": doc, "sizeInBytes": 10},
        "storageFingerprint": {
            "version": 1, "value": fp, "verified": trusted, "trusted": trusted,
            "object": {"size": 10, "etag": "abc"},
        },
    }
    info = parse_document_info(body, verify=True)
    assert info is not None
    return info


def test_golden_tags() -> None:
    original = content_tag("rec-1", _info(), "original")
    pdf = content_tag("rec-1", _info(), "pdf", pipeline=PIPELINE)
    assert original == content_tag("rec-1", _info(), "original")
    assert original.startswith('"') and original.endswith('"') and len(original) == 24
    assert pdf.startswith('W/"') and pdf.endswith('"')
    # Pinned: a change of the basis, the encoding or the version changes every client's tag.
    assert original == GOLDEN_ORIGINAL
    assert pdf == GOLDEN_PDF


@pytest.mark.parametrize(
    "other",
    [
        {"info": _info(fp="fp-2")},
        {"info": _info(doc="doc-2")},
        {"record_id": "rec-2"},
        {"variant": "pdf", "pipeline": PIPELINE},
    ],
    ids=["fingerprint", "document", "record", "variant"],
)
def test_the_tag_moves_with_its_basis(other) -> None:
    base = {"record_id": "rec-1", "info": _info(), "variant": "original"}
    assert content_tag(**{**base, **other}) != content_tag(**base)


def test_the_pdf_tag_moves_with_the_pipeline() -> None:
    a = content_tag("rec-1", _info(), "pdf", pipeline="lo:7.6|conv:1")
    b = content_tag("rec-1", _info(), "pdf", pipeline="lo:24.2|conv:1")
    assert a != b


def test_no_tag_without_a_trusted_storage_answer() -> None:
    assert content_tag("rec-1", None, "original") is None
    assert content_tag("rec-1", _info(trusted=False), "original") is None
    # A converted answer without a known converter has no tag.
    assert content_tag("rec-1", _info(), "pdf") is None


def test_the_tag_does_not_depend_on_index_state() -> None:
    # Nothing about contentRev, externalRevisionId or the record's size enters the basis: the function
    # takes none of them, so a re-index cannot move it.
    import inspect

    params = set(inspect.signature(content_tag).parameters)
    assert params == {"record_id", "info", "variant", "pipeline"}


@pytest.mark.parametrize(
    "header,tag,expected",
    [
        ('"abc"', '"abc"', True),
        ('W/"abc"', '"abc"', True),
        ('"abc"', 'W/"abc"', True),
        ('"x", "abc"', '"abc"', True),
        ('"x" , W/"abc" ', '"abc"', True),
        ("*", '"abc"', True),
        ('"abd"', '"abc"', False),
        ("", '"abc"', False),
        (None, '"abc"', False),
        ("abc", '"abc"', False),
        ('"abc', '"abc"', False),
        ("*", None, False),
        ('"abc"', None, False),
    ],
)
def test_if_none_match_uses_weak_comparison(header, tag, expected) -> None:
    assert if_none_match_matches(header, tag) is expected


GOLDEN_ORIGINAL = '"4jecPqfg47AjQX_WP15zjJ"'
GOLDEN_PDF = 'W/"lqzg5ssRs9bTEhADiLkGO7"'
