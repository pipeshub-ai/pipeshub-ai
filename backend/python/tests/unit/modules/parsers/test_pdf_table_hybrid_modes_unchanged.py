"""``legacy``, ``evidence`` and ``shadow`` return what they returned before the emit phase existed. The hashes were taken
from the shadow slice's commit on the committed fixture; ``legacy`` imports none of the emit modules."""
from __future__ import annotations

import subprocess
import sys

import pytest

from app.modules.parsers.pdf import opencv_layout_analyzer as ola
from app.modules.parsers.pdf import table_reconcile
from app.modules.parsers.pdf.layout_pages import parse_pdf_layout
from tests.support.table_emit import (
    FIXTURES,
    canonical,
    fake_boxes,
    fixture_pdf,
    make_pdf,
    prose_page,
)

PDF, BOXES = fixture_pdf()
GOLDEN = {
    "legacy": "d8d7e5da9873c44426d2ce5d6739c1d550c13dd6342c051fe043d9afe7b64b91",
    "shadow": "d8d7e5da9873c44426d2ce5d6739c1d550c13dd6342c051fe043d9afe7b64b91",
    "evidence": "9d5155643f73b4472d83c04063fac15bd1d83cf9ac124eca2f70433c9181fc28",
}


@pytest.mark.parametrize("mode", sorted(GOLDEN))
def test_the_mode_returns_the_regions_it_returned_before(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(ola, "_detect_model_boxes", fake_boxes(BOXES))
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", mode)
    assert canonical(parse_pdf_layout(PDF)) == GOLDEN[mode]


def test_the_default_mode_is_legacy_and_hybrid_is_never_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PDF_TABLE_EXTRACTION", raising=False)
    assert table_reconcile.table_extraction_mode() == "legacy" and table_reconcile.DEFAULT_MODE == "legacy"
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", "hybrid")
    assert table_reconcile.table_extraction_mode() == "hybrid"
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", "hybrid2")
    assert table_reconcile.table_extraction_mode() == "legacy"


def test_a_legacy_parse_imports_none_of_the_emit_modules() -> None:
    code = (
        "import os, sys; os.environ.pop('PDF_TABLE_EXTRACTION', None)\n"
        "from app.modules.parsers.pdf.layout_pages import parse_pdf_layout\n"
        f"parse_pdf_layout(open({str(FIXTURES / 'tables.pdf')!r}, 'rb').read())\n"
        "bad = [m for m in ('app.modules.parsers.pdf.table_hybrid_emit', 'app.modules.parsers.pdf.table_hybrid_text', 'app.modules.parsers.pdf.table_hybrid_sync_client') if m in sys.modules]\n"
        "print('IMPORTED', bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120)
    assert "IMPORTED []" in out.stdout, out.stdout + out.stderr


PROSE_GOLDEN = "8c6be9f9a534715a43c6b64bdc4061593aaa5c9e5cda65566e3073e839cc8c1f"


@pytest.mark.parametrize("mode", ["legacy", "evidence", "shadow"])
def test_a_prose_page_goes_through_the_tail_as_before(monkeypatch: pytest.MonkeyPatch, tmp_path, mode: str) -> None:
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", mode)
    assert canonical(parse_pdf_layout(make_pdf(prose_page, tmp_path / "prose.pdf"))) == PROSE_GOLDEN
