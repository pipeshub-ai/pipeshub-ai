"""The cost-class corpus is seeded, labelled by class, and every file is what its label says."""

from __future__ import annotations

import io
import time
import zipfile
from typing import TYPE_CHECKING

import pytest

from tests.perf.parse_bench import corpus

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

CLASSES = {"small-text", "small-pdf", "office", "scanned", "large-pdf", "huge-pdf"}


@pytest.fixture(scope="module")
def smoke(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, corpus.Manifest]:
    out = tmp_path_factory.mktemp("cost-smoke")
    return out, corpus.generate(out, profile="cost-smoke", seed=11)


def test_same_seed_same_bytes(smoke: tuple[Path, corpus.Manifest], tmp_path: Path) -> None:
    # More than a second apart: a clock stamped into a package (the office formats' core.xml) shows up here.
    time.sleep(1.2)
    again = corpus.generate(tmp_path, profile="cost-smoke", seed=11)
    assert [e.sha256 for e in again.entries] == [e.sha256 for e in smoke[1].entries]
    assert again.corpus_id == smoke[1].corpus_id


@pytest.mark.parametrize("seed", [11, 2026, 424242])
def test_office_packages_carry_a_seed_derived_date_and_nothing_else_from_the_clock(seed: int) -> None:
    """core.xml dates and every zip entry time are a function of the seed alone (xlsx stamps ``now`` at save)."""
    import random

    stamp = corpus.seed_stamp(seed)
    first = corpus.xlsx_small(corpus.Words(random.Random(seed)), 5, stamp)
    time.sleep(1.1)
    assert corpus.xlsx_small(corpus.Words(random.Random(seed)), 5, stamp) == first
    for data in (first, corpus.docx_file(corpus.Words(random.Random(seed)), 1, stamp),
                 corpus.pptx_file(corpus.Words(random.Random(seed)), 2, stamp)):
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            assert {i.date_time for i in zf.infolist()} == {
                (stamp.year, stamp.month, stamp.day, stamp.hour, stamp.minute, stamp.second - stamp.second % 2)}
            if "docProps/core.xml" in zf.namelist():
                core = zf.read("docProps/core.xml").decode()
                assert core.count(stamp.strftime("%Y-%m-%dT%H:%M:%SZ")) >= 2, core
    assert corpus.seed_stamp(seed) != corpus.seed_stamp(seed + 1)


def test_other_seed_other_bytes(smoke: tuple[Path, corpus.Manifest], tmp_path: Path) -> None:
    assert corpus.generate(tmp_path, profile="cost-smoke", seed=12).corpus_id != smoke[1].corpus_id


def test_entries_carry_class_and_pages(smoke: tuple[Path, corpus.Manifest]) -> None:
    kinds = {e.kind for e in smoke[1].entries}
    assert kinds <= CLASSES
    assert {"small-text", "small-pdf", "office", "scanned"} <= kinds
    assert all(e.pages >= 1 for e in smoke[1].entries if e.kind in {"small-pdf", "scanned", "office"})
    assert {e.ext for e in smoke[1].entries if e.kind == "office"} == {"docx", "xlsx", "pptx"}


def test_page_count_oracle(smoke: tuple[Path, corpus.Manifest]) -> None:
    """Every PDF re-opens with exactly the page count the manifest claims."""
    import pypdfium2 as pdfium
    pdfs = [e for e in smoke[1].entries if e.ext == "pdf"]
    assert pdfs
    for entry in pdfs:
        doc = pdfium.PdfDocument((smoke[0] / entry.path).read_bytes())
        assert len(doc) == entry.pages, entry.path


def test_huge_pdf_has_a_thousand_pages(tmp_path: Path) -> None:
    import pypdfium2 as pdfium
    data = corpus.light_pdf(corpus.Words(__import__("random").Random(3)), 1000)
    assert len(pdfium.PdfDocument(data)) == 1000


def test_office_files_open(smoke: tuple[Path, corpus.Manifest]) -> None:
    """The three office formats open with their own readers, and the slide count is the manifest's."""
    import docx
    import openpyxl
    from pptx import Presentation

    seen = set()
    for entry in (e for e in smoke[1].entries if e.kind == "office"):
        data = (smoke[0] / entry.path).read_bytes()
        seen.add(entry.ext)
        if entry.ext == "docx":
            assert docx.Document(io.BytesIO(data)).paragraphs
        elif entry.ext == "xlsx":
            assert openpyxl.load_workbook(io.BytesIO(data)).active.max_row > 1
        else:
            assert len(Presentation(io.BytesIO(data)).slides) == entry.pages
    assert seen == {"docx", "xlsx", "pptx"}


def test_pptx_is_a_valid_package() -> None:
    words = corpus.Words(__import__("random").Random(5))
    data = corpus.pptx_file(words, 7)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist()[0] == "[Content_Types].xml"
        assert zf.testzip() is None
        assert len([n for n in zf.namelist() if n.startswith("ppt/slides/slide")]) == 7
    assert corpus.pptx_file(corpus.Words(__import__("random").Random(5)), 7) == data


def test_profiles_are_registered() -> None:
    assert set(corpus.COST_PROFILES) == {"cost-smoke", "cost-standard", "cost-huge"}
    assert corpus.COST_PROFILES["cost-huge"]["huge-pdf"][0] == 10
    assert corpus.COST_PROFILES["cost-smoke"]["huge-pdf"][0] == 0


def test_page_counts_are_spread_not_constant(tmp_path: Path) -> None:
    """Small PDFs differ in length (1..20 pages), so a per-kind number is not one page size's number."""
    manifest = corpus.generate(tmp_path, profile="cost-standard", seed=11, scale=0.5)
    pages = sorted({e.pages for e in manifest.entries if e.kind == "small-pdf"})
    assert len(pages) > 1 and pages[0] >= 1 and pages[-1] <= 20, pages
    assert corpus._spread(3, 20) != corpus._spread(4, 20)
