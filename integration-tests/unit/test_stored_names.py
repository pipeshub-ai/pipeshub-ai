"""What PipesHub stores as a record's name, given the uploaded file name."""

from __future__ import annotations

import pytest

from helper.stored_names import stored_extension, stored_name

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("uploaded", "stored"),
    [
        ("runbook-abc123.md", "runbook-abc123"),
        ("board-pack.pdf", "board-pack"),
        ("notes.tar.gz", "notes.tar"),
        ("README", "README"),
        (".gitignore", ".gitignore"),
        ("trailing.", "trailing."),
    ],
)
def test_the_stored_name_drops_only_the_final_extension(uploaded: str, stored: str) -> None:
    assert stored_name(uploaded) == stored


@pytest.mark.parametrize(
    ("uploaded", "extension"),
    [
        ("runbook-abc123.md", "md"),
        ("board-pack.PDF", "pdf"),
        ("notes.tar.gz", "gz"),
        ("README", None),
        (".gitignore", None),
        ("trailing.", None),
    ],
)
def test_the_stored_extension_is_lower_cased_and_dotless(uploaded: str, extension: str | None) -> None:
    assert stored_extension(uploaded) == extension
