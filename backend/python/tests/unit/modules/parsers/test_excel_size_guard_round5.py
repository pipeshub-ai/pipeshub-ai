"""Round-5 inputs: tiny files that expand to gigabytes when openpyxl builds objects for them.

Rewritten for the worker (see test_excel_size_guard_bypasses.py): each input is built at a
size the 32 MB test allowance cannot hold (scaled from the full-size measurements in
notes/qa/mem6-r5-report.md) and must be refused with EXCEL_TOO_LARGE, however small the file.
The formula-heavy workbook the old guard refused although it loaded must load.
"""

import io
import struct
import zipfile
import zlib

import openpyxl
import pytest
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image

from app.modules.parsers.excel import excel_parser as module
from app.services.parsing.interface import ParseError
from app.utils import user_errors
from tests.support.excel_workbooks import (
    MAIN_NS,
    REL_NS,
    empty_cells,
    load,
    workbook_zip,
)


@pytest.fixture(autouse=True)
def small_worker_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker may grow by 32 MB, so a few hundred thousand cells stand in for the gigabytes."""
    monkeypatch.setattr(module, "EXCEL_LOAD_MAX_BYTES", 32 * 1024 * 1024)


def assert_refused_as_too_large(data: bytes) -> None:
    with pytest.raises(ParseError) as raised:
        load(data)
    assert raised.value.message == user_errors.EXCEL_TOO_LARGE



@pytest.mark.parametrize("tag", ["mergeCell", "hyperlink"])
def test_one_range_element_covering_many_cells_is_refused(tag: str) -> None:
    # openpyxl creates one cell object per cell of a merged (or hyperlinked) range:
    # <mergeCell ref="A1:J200000"/> (60 bytes) became 2 M objects, 585 MB, in 49 s.
    ref = "A1:Z20000"  # 520 000 cells
    if tag == "mergeCell":
        inner = f'<mergeCells><mergeCell ref="{ref}"/></mergeCells>'
    else:
        inner = f'<hyperlinks><hyperlink ref="{ref}" location="S!A1"/></hyperlinks>'
    assert_refused_as_too_large(workbook_zip("<sheetData/>" + inner))


def test_one_sqref_listing_many_ranges_is_refused_or_cut_short() -> None:
    # A 7-byte "A123 " becomes a CellRange object (about 360 bytes) in a MultiCellRange.
    refs = " ".join(f"A{i}" for i in range(1, 300_001))
    inner = f'<dataValidations count="1"><dataValidation type="whole" sqref="{refs}"><formula1>1</formula1></dataValidation></dataValidations>'
    assert_refused_as_too_large(workbook_zip("<sheetData/>" + inner))


def test_a_conditional_format_over_many_ranges_stays_inside_the_allowance() -> None:
    # openpyxl swallows the MemoryError while building this rule and drops it ("Failed to load a
    # conditional formatting rule"), so the load may succeed without it. Either way the worker
    # must not have grown past the allowance (its peak includes ~50 MB of imports).
    refs = " ".join(f"A{i}" for i in range(1, 300_001))
    inner = f'<conditionalFormatting sqref="{refs}"><cfRule type="cellIs" priority="1" operator="equal"><formula>1</formula></cfRule></conditionalFormatting>'
    try:
        parser = load(workbook_zip("<sheetData/>" + inner))
    except ParseError as refused:
        assert refused.message == user_errors.EXCEL_TOO_LARGE
    else:
        assert parser.workbook.worker_peak_kb < 120 * 1024  # unconstrained it needs about 115 MB more


def _saved(wb: openpyxl.Workbook) -> bytes:
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_comments_are_refused_when_they_do_not_fit() -> None:
    # Measured 200 000 comments: 446 MB, 2.2 KB each.
    wb = openpyxl.Workbook()
    ws = wb.active
    for i in range(1, 25_001):
        ws[f"A{i}"].comment = Comment("hi", "a")
    assert_refused_as_too_large(_saved(wb))


def test_table_columns_are_refused_when_they_do_not_fit() -> None:
    # Measured 200 000 tableColumn: 196 MB, 1 KB each.
    n = 60_000
    cols = "".join(f'<tableColumn id="{i}" name="c{i}"/>' for i in range(1, n + 1))
    table = (
        f'<?xml version="1.0"?><table xmlns="{MAIN_NS}" id="1" name="T" displayName="T" ref="A1:B2">'
        f'<tableColumns count="{n}">{cols}</tableColumns></table>'
    )
    rels = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{REL_NS}/table" Target="../tables/table1.xml"/></Relationships>'
    )
    data = workbook_zip(
        '<sheetData><row r="1"><c r="A1"><v>1</v></c></row></sheetData><tableParts count="1"><tablePart r:id="rId1"/></tableParts>',
        extra={"xl/tables/table1.xml": table},
        sheet_rels=rels,
    )
    assert_refused_as_too_large(data)


def _png(tail: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    head = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
    return head + chunk(b"IDAT", zlib.compress(b"\x00\x00")) + chunk(b"IEND", b"") + b"\0" * tail


def _with_image(png: bytes) -> bytes:
    wb = openpyxl.Workbook()
    wb.active.add_image(Image(io.BytesIO(png)), "A1")
    return _saved(wb)


def test_an_image_that_is_not_xml_is_counted_to_its_end() -> None:
    # openpyxl reads a drawing's image whole and keeps it: a 50 MB picture compresses to a few KB.
    data = _with_image(_png(50_000_000))
    assert len(data) < 200_000
    assert_refused_as_too_large(data)


def test_one_image_referenced_by_many_anchors_is_refused() -> None:
    # openpyxl reads the image bytes again for every anchor that uses it and keeps each copy.
    # 400 anchors on one 1 MB picture hold 400 MB; here the file is the single picture plus a few KB of XML.
    base = _with_image(_png(1_000_000))
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base)) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            body = src.read(info.filename)
            if info.filename == "xl/drawings/drawing1.xml":
                text = body.decode()
                start = text.index("<oneCellAnchor>")
                end = text.index("</oneCellAnchor>") + len("</oneCellAnchor>")
                text = text[:start] + text[start:end] * 100 + text[end:]
                body = text.encode()
            dst.writestr(info, body)
    data = out.getvalue()
    assert len(data) < 200_000
    assert_refused_as_too_large(data)


def _with_unreadable_first_entry(variant: str, rows: int) -> bytes:
    # An entry openpyxl never opens (customXml/item1.xml) that cannot be read, listed first.
    junk = (zipfile.ZipInfo("customXml/item1.xml"), b"<x>" + b"MARKERMARKER" * 20 + b"</x>")
    data = workbook_zip(f"<sheetData>{empty_cells(rows, 80)}</sheetData>", first=[junk])
    if variant == "bad_crc":
        return data.replace(b"MARKERMARKER", b"MARKERMARKEZ", 1)
    buf = bytearray(data)
    central = buf.index(b"PK\x01\x02")
    if variant == "unknown_method":
        buf[8:10] = (99).to_bytes(2, "little")
        buf[central + 10 : central + 12] = (99).to_bytes(2, "little")
    else:  # encrypted flag
        buf[6] |= 1
        buf[central + 8] |= 1
    return bytes(buf)


@pytest.mark.parametrize("variant", ["bad_crc", "unknown_method", "encrypted_flag"])
def test_an_entry_that_cannot_be_read_does_not_hide_the_workbook_behind_it(variant: str) -> None:
    # openpyxl does not open the junk entry and loads the sheet behind it in full.
    assert_refused_as_too_large(_with_unreadable_first_entry(variant, 2000))


def test_calc_chain_does_not_make_formula_workbooks_too_large(monkeypatch: pytest.MonkeyPatch) -> None:
    # This workbook needs about 26 MB of worker growth on one platform, so the file's 32 MB
    # allowance left it a 6 MB margin that an allocator or interpreter difference (CI saw a
    # refusal) can eat. It is a workbook that must load, so it gets an allowance it cannot
    # come near; the refusal tests above keep the small one.
    monkeypatch.setattr(module, "EXCEL_LOAD_MAX_BYTES", 256 * 1024 * 1024)
    # False refusal under the old guard: calcChain.xml (never loaded) cost a cell per formula,
    # so 2 M formula cells (732 MB to load) were refused. Here 40 000 formula cells + calcChain load.
    n = 40_000
    cells = "".join(f'<row r="{i}"><c r="A{i}"><f>B{i}*2</f><v>1</v></c></row>' for i in range(1, n + 1))
    chain = f'<?xml version="1.0"?><calcChain xmlns="{MAIN_NS}">' + "".join(f'<c r="A{i}" i="1"/>' for i in range(1, n + 1)) + "</calcChain>"
    parser = load(workbook_zip(f"<sheetData>{cells}</sheetData>", extra={"xl/calcChain.xml": chain}))
    assert parser.workbook["S"].cell(n, 1).value == 1
