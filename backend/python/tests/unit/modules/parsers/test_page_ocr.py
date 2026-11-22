"""Pages whose text is drawn as outlines inside a text-layer PDF are read with OCR, one page at a time."""
from __future__ import annotations

import io
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from reportlab.pdfgen import canvas

from app.modules.parsers.pdf import page_ocr
from app.modules.parsers.pdf.pdf_inspection import extract_pages, textless_pages
from app.modules.parsers.pdf.pdfplumber_opencv_processor import (
    PDFPlumberOpenCVProcessor,
)

OCR_MARKDOWN = "| Nature of business | Contributions receivable |\n| --- | --- |\n| Total | 32,903,605 |"
TEXT_LINE = "The quarterly report describes revenue, costs and the outlook for the coming year in detail."


def _draw_outlined_text(c: canvas.Canvas, x: float, y: float, glyphs: int = 12) -> None:
    """Glyph-like closed bezier outlines, 30 per glyph: what text converted to curves looks like."""
    for g in range(glyphs):
        for k in range(30):
            ox, oy = x + g * 14 + (k % 6) * 1.5, y + (k // 6) * 1.5
            p = c.beginPath()
            p.moveTo(ox, oy)
            p.curveTo(ox + 4, oy + 3, ox + 6, oy + 1, ox + 7, oy - 2)
            p.curveTo(ox + 5, oy - 5, ox + 2, oy - 4, ox, oy)
            c.drawPath(p, stroke=1, fill=0)


def _text_page(c: canvas.Canvas, lines: int = 12) -> None:
    for i in range(lines):
        c.drawString(72, 760 - i * 16, TEXT_LINE)
    c.showPage()


def make_pdf(pages: list[str], glyphs: int = 12) -> bytes:
    """Pages of 'text' (ordinary), 'outlined' (text as paths, a short footer) or 'blank'."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    for kind in pages:
        if kind == "text":
            _text_page(c)
        elif kind == "outlined":
            c.drawString(72, 40, "Schedule footer, page 93")
            _draw_outlined_text(c, 72, 600, glyphs)
            c.showPage()
        else:
            c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture
def pdf() -> bytes:
    return make_pdf(["text", "outlined", "text"])


def test_detector_picks_only_the_outlined_page(pdf: bytes) -> None:
    assert textless_pages(pdf, 25) == ([2], 1)


def test_detector_ignores_text_blank_and_ruled_pages() -> None:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    for i in range(20):  # a ruled form with a short caption: a few lines, no heavy drawing
        c.line(72, 700 - i * 10, 500, 700 - i * 10)
    c.drawString(72, 40, "Form 7")
    c.showPage()
    c.showPage()  # blank
    _text_page(c)
    c.save()
    assert textless_pages(buf.getvalue(), 25) == ([], 0)


def test_cap_keeps_the_pages_with_most_drawn_content_in_page_order() -> None:
    pdf = make_pdf(["outlined", "text", "outlined", "outlined"])
    numbers, found = textless_pages(pdf, 2)
    assert found == 3
    assert numbers == sorted(numbers) and len(numbers) == 2


def test_extract_pages_keeps_requested_pages_in_order(pdf: bytes) -> None:
    import pypdfium2

    sub = pypdfium2.PdfDocument(extract_pages(pdf, [3, 2]))
    assert len(sub) == 2
    sub.close()


def _stub_handler(markdown: str = OCR_MARKDOWN) -> MagicMock:
    handler = MagicMock()

    async def process(content: bytes) -> dict:
        import pypdfium2

        n = len(pypdfium2.PdfDocument(content))
        return {"pages": [{"page_number": i + 1, "markdown": markdown} for i in range(n)]}

    handler.process_document = AsyncMock(side_effect=process)
    return handler


async def _parse(pdf: bytes) -> tuple[PDFPlumberOpenCVProcessor, list]:
    proc = PDFPlumberOpenCVProcessor(logger=logging.getLogger("t"), config=MagicMock())
    return proc, await proc.parse_document("x.pdf", pdf)


async def _texts(proc: PDFPlumberOpenCVProcessor, parsed, page: int) -> list[str]:
    container = await proc.create_blocks(parsed, skip_llm_enrichment=True)
    return [b.data for b in container.blocks if b.citation_metadata and b.citation_metadata.page_number == page]


@pytest.mark.asyncio
async def test_outlined_page_is_indexed_through_ocr(pdf: bytes) -> None:
    proc, parsed = await _parse(pdf)
    # Without the step the page holds no figure text at all (the bug).
    assert not any("32,903,605" in t for t in await _texts(proc, parsed, 2))
    handler = _stub_handler()
    with patch.object(page_ocr, "_vlm_ocr_handler", AsyncMock(return_value=handler)):
        replaced = await page_ocr.ocr_textless_pages(pdf, parsed, MagicMock(), logging.getLogger("t"))
    assert replaced == [2]
    assert any("32,903,605" in str(t) for t in await _texts(proc, parsed, 2))
    handler.process_document.assert_awaited_once()


@pytest.mark.asyncio
async def test_text_pages_are_untouched_and_blocks_identical(pdf: bytes) -> None:
    proc, parsed = await _parse(pdf)
    before = await proc.create_blocks(parsed, skip_llm_enrichment=True)
    with patch.object(page_ocr, "_vlm_ocr_handler", AsyncMock(return_value=_stub_handler())):
        await page_ocr.ocr_textless_pages(pdf, parsed, MagicMock(), logging.getLogger("t"))
    after = await proc.create_blocks(parsed, skip_llm_enrichment=True)

    def page_of(b: object) -> int:
        return b.citation_metadata.page_number

    for page in (1, 3):
        old = [b.model_dump(exclude={"id"}) for b in before.blocks if page_of(b) == page]
        new = [b.model_dump(exclude={"id"}) for b in after.blocks if page_of(b) == page]
        assert old and old == new
    # Block indices stay a running count and page order is kept.
    assert [b.index for b in after.blocks] == list(range(len(after.blocks)))
    assert [page_of(b) for b in after.blocks] == sorted(page_of(b) for b in after.blocks)


@pytest.mark.asyncio
async def test_text_only_document_makes_no_ocr_call() -> None:
    pdf = make_pdf(["text", "text"])
    _, parsed = await _parse(pdf)
    factory = AsyncMock(return_value=_stub_handler())
    with patch.object(page_ocr, "_vlm_ocr_handler", factory):
        assert await page_ocr.ocr_textless_pages(pdf, parsed, MagicMock(), logging.getLogger("t")) == []
    factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_cap_is_respected_and_logged(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    pdf = make_pdf(["outlined"] * 4 + ["text"])
    _, parsed = await _parse(pdf)
    monkeypatch.setenv(page_ocr.PAGE_OCR_MAX_PAGES_ENV, "2")
    handler = _stub_handler()
    with patch.object(page_ocr, "_vlm_ocr_handler", AsyncMock(return_value=handler)), caplog.at_level(logging.INFO):
        replaced = await page_ocr.ocr_textless_pages(pdf, parsed, MagicMock(), logging.getLogger("t"))
    assert len(replaced) == 2
    import pypdfium2

    sent = handler.process_document.await_args.args[0]
    assert len(pypdfium2.PdfDocument(sent)) == 2
    assert any("cap" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_cap_zero_turns_the_step_off(monkeypatch: pytest.MonkeyPatch, pdf: bytes) -> None:
    monkeypatch.setenv(page_ocr.PAGE_OCR_MAX_PAGES_ENV, "0")
    _, parsed = await _parse(pdf)
    factory = AsyncMock()
    with patch.object(page_ocr, "_vlm_ocr_handler", factory):
        assert await page_ocr.ocr_textless_pages(pdf, parsed, MagicMock(), logging.getLogger("t")) == []
    factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_provider_changes_nothing_and_logs_once(pdf: bytes, caplog: pytest.LogCaptureFixture) -> None:
    proc, parsed = await _parse(pdf)
    before = await proc.create_blocks(parsed, skip_llm_enrichment=True)
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"ocr": [], "llm": []})
    with caplog.at_level(logging.INFO):
        assert await page_ocr.ocr_textless_pages(pdf, parsed, config, logging.getLogger("t")) == []
    after = await proc.create_blocks(parsed, skip_llm_enrichment=True)
    assert [b.data for b in before.blocks] == [b.data for b in after.blocks]
    assert sum("no OCR provider" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.asyncio
async def test_ocr_failure_keeps_the_pages(pdf: bytes) -> None:
    _, parsed = await _parse(pdf)
    handler = MagicMock()
    handler.process_document = AsyncMock(side_effect=RuntimeError("model down"))
    with patch.object(page_ocr, "_vlm_ocr_handler", AsyncMock(return_value=handler)):
        assert await page_ocr.ocr_textless_pages(pdf, parsed, MagicMock(), logging.getLogger("t")) == []


@pytest.mark.asyncio
async def test_processor_route_indexes_the_outlined_page(pdf: bytes) -> None:
    """process_pdf_with_pdf_plumber hands the page's OCR text to indexing."""
    from tests.unit.events.test_processor_deep import _make_processor

    proc = _make_processor()
    proc.graph_provider.get_document = AsyncMock(return_value={"_key": "r1"})
    captured = SimpleNamespace()
    proc._convert_record = lambda r: captured
    proc._create_transform_context = MagicMock(return_value=MagicMock())
    with patch.object(page_ocr, "_vlm_ocr_handler", AsyncMock(return_value=_stub_handler())), \
         patch("app.events.processor.get_llm_for_role", AsyncMock(side_effect=RuntimeError("none"))), \
         patch("app.events.processor.IndexingPipeline") as pipeline:
        pipeline.return_value.apply = AsyncMock()
        async for _ in proc.process_pdf_with_pdf_plumber("x.pdf", "r1", pdf, "vr1"):
            pass
    texts = [str(b.data) for b in captured.block_containers.blocks]
    assert any("32,903,605" in t for t in texts)


def _chart_pdf() -> bytes:
    """A text document whose page 2 is a picture of a chart with its labels drawn as outlines."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    picture = Image.new("RGB", (200, 120), (30, 90, 200))
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    _text_page(c)
    c.drawImage(ImageReader(picture), 72, 500, width=300, height=180)
    _draw_outlined_text(c, 72, 400)
    c.showPage()
    _text_page(c)
    c.save()
    return buf.getvalue()


@pytest.mark.asyncio
async def test_pictures_on_an_ocr_page_stay_and_text_is_replaced() -> None:
    from app.models.blocks import BlockType

    pdf = _chart_pdf()
    proc, parsed = await _parse(pdf)
    before = await proc.create_blocks(parsed, skip_llm_enrichment=True)
    images_before = [b for b in before.blocks if b.type == BlockType.IMAGE and b.citation_metadata.page_number == 2]
    assert images_before, "the fixture must hold a picture on page 2"
    with patch.object(page_ocr, "_vlm_ocr_handler", AsyncMock(return_value=_stub_handler())):
        assert await page_ocr.ocr_textless_pages(pdf, parsed, MagicMock(), logging.getLogger("t")) == [2]
    after = await proc.create_blocks(parsed, skip_llm_enrichment=True)
    page2 = [b for b in after.blocks if b.citation_metadata.page_number == 2]
    assert len([b for b in page2 if b.type == BlockType.IMAGE]) == len(images_before)
    assert any("32,903,605" in str(b.data) for b in page2 if b.type != BlockType.IMAGE)


def test_ocr_text_takes_the_place_of_the_first_replaced_region() -> None:
    from app.modules.parsers.pdf.opencv_layout_analyzer import (
        LayoutRegion,
        LayoutRegionType,
    )

    def region(kind: LayoutRegionType, text: str) -> LayoutRegion:
        return LayoutRegion(type=kind, bbox=(0, 0, 1, 1), text=text)

    img, tbl = LayoutRegionType.IMAGE, LayoutRegionType.TABLE
    txt = LayoutRegionType.TEXT
    old = [region(img, "i1"), region(txt, "a"), region(img, "i2"), region(tbl, "t"), region(txt, "b")]
    new = [region(txt, "OCR1"), region(txt, "OCR2")]
    assert [r.text for r in page_ocr._with_ocr_text(old, new)] == ["i1", "OCR1", "OCR2", "i2"]
    only_pictures = [region(img, "i1"), region(img, "i2")]
    assert [r.text for r in page_ocr._with_ocr_text(only_pictures, new)] == ["i1", "i2", "OCR1", "OCR2"]


def _fake_page(chars_drawn: int = 0, image_share: float = 0.0) -> SimpleNamespace:
    images = [{"x0": 0, "x1": 100 * image_share, "top": 0, "bottom": 100}] if image_share else []
    return SimpleNamespace(
        curves=[0] * chars_drawn, rects=[], lines=[], images=images, width=100.0, height=100.0
    )


def test_score_boundaries() -> None:
    from app.modules.parsers.pdf import pdf_inspection as pi

    score = pi.textless_page_score
    # Characters: the ceiling is exclusive.
    assert score(_fake_page(500), pi.TEXTLESS_PAGE_MAX_CHARS - 1) > 0
    assert score(_fake_page(500), pi.TEXTLESS_PAGE_MAX_CHARS) == 0
    assert score(_fake_page(0, 1.0), pi.TEXTLESS_PAGE_MAX_CHARS) == 0
    # Drawn objects: more than the floor.
    assert score(_fake_page(pi.TEXTLESS_PAGE_MIN_DRAWN), 10) == 0
    assert score(_fake_page(pi.TEXTLESS_PAGE_MIN_DRAWN + 1), 10) == pi.TEXTLESS_PAGE_MIN_DRAWN + 1
    # Image share: at the threshold counts, just under does not.
    assert score(_fake_page(0, 0.75), 10) > 0
    assert score(_fake_page(0, 0.74), 10) == 0
    assert score(_fake_page(0, 0.5), 10) == 0
    # A mostly-image page the share rule alone selects, however much it draws below the floor.
    assert score(_fake_page(5, 0.9), 0) > 0


def test_cap_keeps_the_highest_scoring_pages() -> None:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    for glyphs in (8, 14, 10):  # page 2 draws the most, then page 3, then page 1
        c.drawString(72, 40, "Schedule footer")
        _draw_outlined_text(c, 72, 600, glyphs)
        c.showPage()
    c.save()
    assert textless_pages(buf.getvalue(), 1) == ([2], 3)
    assert textless_pages(buf.getvalue(), 2) == ([2, 3], 3)


def test_a_text_page_with_a_full_page_picture_is_not_selected() -> None:
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    c.drawImage(ImageReader(Image.new("RGB", (50, 50), (200, 200, 200))), 0, 0, width=612, height=792)
    for i in range(12):
        c.drawString(72, 760 - i * 16, TEXT_LINE)
    c.showPage()
    c.save()
    assert textless_pages(buf.getvalue(), 25) == ([], 0)


def test_a_mostly_image_page_with_a_short_caption_is_selected() -> None:
    from PIL import Image
    from reportlab.lib.utils import ImageReader

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    c.drawImage(ImageReader(Image.new("RGB", (50, 50), (200, 200, 200))), 0, 0, width=612, height=792)
    c.drawString(72, 40, "Fig 1")
    c.showPage()
    c.save()
    assert textless_pages(buf.getvalue(), 25) == ([1], 1)


def test_overlapping_images_count_their_shared_area_once() -> None:
    from app.modules.parsers.pdf import pdf_inspection as pi

    half = {"x0": 0, "x1": 50, "top": 0, "bottom": 100}
    twice = SimpleNamespace(curves=[], rects=[], lines=[], images=[half, dict(half)], width=100.0, height=100.0)
    assert pi.textless_page_score(twice, 10) == 0
    # Images are clipped to the page: one bleeding past every edge covers the page once.
    bleed = {"x0": -50, "x1": 150, "top": -50, "bottom": 150}
    page = SimpleNamespace(curves=[], rects=[], lines=[], images=[bleed, dict(bleed)], width=100.0, height=100.0)
    assert pi.textless_page_score(page, 10) == 1000
    assert pi.textless_page_score(_fake_page(0, 1.0), 10) == 1000


def _ocr_pages(paragraphs_of: list[list[str]]) -> list:
    from app.modules.parsers.pdf.layout_pages import ParsedPageData

    pages = []
    for n, paragraphs in enumerate(paragraphs_of, start=1):
        page = ParsedPageData(page_number=n, width=612.0, height=792.0, regions=[])
        page.regions = page_ocr.regions_from_markdown("\n\n".join(paragraphs), page)
        pages.append(page)
    return pages


def test_ocr_paragraphs_are_not_taken_for_margin_furniture() -> None:
    from app.modules.parsers.pdf.pdfplumber_opencv_processor import running_noise

    # Lone numbers at the start, middle and end of each page, and a short line on every page.
    pages = _ocr_pages([["2024", "Schedule of grants", str(125 + n * 7), "Totals by region", "48"] for n in range(6)])
    assert running_noise(pages) == set()
    # The OCR text keeps the page's box for citations.
    assert all(r.bbox == (0.0, 0.0, 612.0, 792.0) for p in pages for r in p.regions)


def test_printed_page_numbers_read_by_ocr_still_drop() -> None:
    from app.modules.parsers.pdf.pdfplumber_opencv_processor import running_noise

    pages = _ocr_pages([["Schedule of grants", f"Page {n + 10}"] for n in range(1, 5)])
    assert running_noise(pages) == {id(p.regions[-1]) for p in pages}


def test_parsed_page_numbers_in_the_margin_still_drop() -> None:
    from app.modules.parsers.pdf.layout_pages import ParsedPageData
    from app.modules.parsers.pdf.opencv_layout_analyzer import (
        LayoutRegion,
        LayoutRegionType,
    )
    from app.modules.parsers.pdf.pdfplumber_opencv_processor import running_noise

    footer = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(50, 760, 300, 772), text="7")
    body = LayoutRegion(type=LayoutRegionType.TEXT, bbox=(50, 300, 300, 320), text="Revenue rose.")
    page = ParsedPageData(page_number=1, width=612.0, height=792.0, regions=[body, footer])
    assert running_noise([page]) == {id(footer)}


@pytest.mark.asyncio
async def test_unreadable_ai_settings_are_raised_for_a_retry(pdf: bytes) -> None:
    from app.exceptions.indexing_exceptions import AISettingsUnavailableError

    _, parsed = await _parse(pdf)
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=ConnectionError("store down"))
    with pytest.raises(AISettingsUnavailableError):
        await page_ocr.ocr_textless_pages(pdf, parsed, config, logging.getLogger("t"))
    handler = MagicMock()
    handler.process_document = AsyncMock(side_effect=AISettingsUnavailableError("store down"))
    with patch.object(page_ocr, "_vlm_ocr_handler", AsyncMock(return_value=handler)), \
         pytest.raises(AISettingsUnavailableError):
        await page_ocr.ocr_textless_pages(pdf, parsed, MagicMock(), logging.getLogger("t"))


@pytest.mark.asyncio
async def test_no_multimodal_model_keeps_the_pages(pdf: bytes) -> None:
    from app.exceptions.indexing_exceptions import MultimodalModelNotConfiguredError

    _, parsed = await _parse(pdf)
    handler = MagicMock()
    handler.process_document = AsyncMock(side_effect=MultimodalModelNotConfiguredError("none"))
    with patch.object(page_ocr, "_vlm_ocr_handler", AsyncMock(return_value=handler)):
        assert await page_ocr.ocr_textless_pages(pdf, parsed, MagicMock(), logging.getLogger("t")) == []
