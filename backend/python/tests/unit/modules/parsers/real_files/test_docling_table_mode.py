"""Docling's table-structure model runs in ACCURATE mode unless PDF_DOCLING_TABLE_MODE says ``fast``.

FAST can split a ruled table with multi-line cells into extra rows; ACCURATE is Docling's own default.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import TableFormerMode

from app.modules.parsers.pdf import docling_processor

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

ENV = "PDF_DOCLING_TABLE_MODE"


@pytest.fixture
def table_mode_of_converter(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str | None], TableFormerMode]]:
    def build(value: str | None) -> TableFormerMode:
        if value is None:
            monkeypatch.delenv(ENV, raising=False)
        else:
            monkeypatch.setenv(ENV, value)
        docling_processor._get_converter.cache_clear()
        options = docling_processor._get_converter().format_to_options[InputFormat.PDF].pipeline_options
        return options.table_structure_options.mode

    yield build
    docling_processor._get_converter.cache_clear()


def test_default_is_accurate(table_mode_of_converter: Callable[[str | None], TableFormerMode]) -> None:
    assert table_mode_of_converter(None) is TableFormerMode.ACCURATE


@pytest.mark.parametrize("value", ["", "  "])
def test_blank_value_is_the_default(
    table_mode_of_converter: Callable[[str | None], TableFormerMode], value: str
) -> None:
    assert table_mode_of_converter(value) is TableFormerMode.ACCURATE


@pytest.mark.parametrize("value", ["fast", "FAST", " Fast "])
def test_fast_reaches_the_converter(
    table_mode_of_converter: Callable[[str | None], TableFormerMode], value: str
) -> None:
    assert table_mode_of_converter(value) is TableFormerMode.FAST


def test_accurate_reaches_the_converter(table_mode_of_converter: Callable[[str | None], TableFormerMode]) -> None:
    assert table_mode_of_converter("accurate") is TableFormerMode.ACCURATE


def test_invalid_value_warns_and_uses_accurate(
    table_mode_of_converter: Callable[[str | None], TableFormerMode], caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        mode = table_mode_of_converter("turbo-invalid")
    assert mode is TableFormerMode.ACCURATE
    assert any(ENV in r.getMessage() and "turbo-invalid" in r.getMessage() for r in caplog.records)
