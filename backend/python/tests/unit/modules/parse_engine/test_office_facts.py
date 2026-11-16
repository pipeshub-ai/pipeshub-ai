"""The cheap Office facts: page and slide counts, a workbook's cell area, and a bounded, best-effort read."""
from __future__ import annotations

import io
import zipfile

from app.modules.parse_engine.cost import CostClass, CostInputs, FormatKind, estimate
from app.modules.parse_engine.office_facts import OfficeFacts, office_facts

MIB = 1 << 20


def _zip(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _docx(pages: int) -> bytes:
    return _zip({"[Content_Types].xml": b"<x/>", "docProps/app.xml": f"<Properties><Pages>{pages}</Pages></Properties>".encode()})


def _xlsx(*dims: str) -> bytes:
    members = {"xl/workbook.xml": b"<workbook/>"}
    for i, d in enumerate(dims, 1):
        members[f"xl/worksheets/sheet{i}.xml"] = f'<worksheet><dimension ref="{d}"/><sheetData/></worksheet>'.encode()
    return _zip(members)


def test_a_docx_reports_its_pages_and_a_pptx_its_slides() -> None:
    assert office_facts(_docx(420)) == OfficeFacts(pages=420)
    pptx = _zip({"docProps/app.xml": b"<Properties><Slides>35</Slides></Properties>"})
    assert office_facts(pptx) == OfficeFacts(pages=35)


def test_a_workbook_reports_the_area_of_its_sheets() -> None:
    assert office_facts(_xlsx("A1:Z1000", "A1:B10")) == OfficeFacts(cells=26_000 + 20)
    assert office_facts(_xlsx("C3")) == OfficeFacts(cells=1)


def test_anything_unreadable_is_no_facts() -> None:
    for content in (b"", b"not a zip", _zip({"x": b"y"}), _zip({"docProps/app.xml": b"<Properties/>"}), b"PK\x03\x04" + b"\0" * 50):
        assert office_facts(content) == OfficeFacts()


def test_a_member_that_does_not_inflate_is_no_facts_and_never_raises() -> None:
    good = _xlsx("A1:B2")
    junk_name = b"xl/worksheets/sheet1.xml"
    start = good.index(junk_name) + len(junk_name)
    corrupt = good[:start] + b"\xff" * 20 + good[start + 20:]  # damage the compressed payload, keep the directory
    assert office_facts(corrupt) == OfficeFacts()
    docx = _docx(10)
    name = b"docProps/app.xml"
    at = docx.index(name) + len(name)
    assert office_facts(docx[:at] + b"\xff" * 12 + docx[at + 12:]) == OfficeFacts()


def test_the_member_cap_is_applied_from_the_directory_record_before_any_entry_is_parsed(monkeypatch) -> None:
    import zipfile as zf

    def explode(*_a: object, **_k: object) -> None:
        raise AssertionError("the central directory was parsed")

    monkeypatch.setattr(zf.ZipFile, "_RealGetContents", explode)
    assert office_facts(_zip({f"m{i}": b"" for i in range(5001)})) == OfficeFacts()


def test_a_package_with_too_many_members_is_not_inspected() -> None:
    assert office_facts(_zip({f"m{i}": b"" for i in range(5001)})) == OfficeFacts()


def test_facts_correct_a_prior_that_would_leave_a_big_document_small() -> None:
    # No size, so the prior alone is small; the page count says it is not.
    assert estimate(CostInputs(FormatKind.DOCX, None)).cls is CostClass.SMALL
    assert estimate(CostInputs(FormatKind.DOCX, None, pages=office_facts(_docx(2000)).pages)).cls is not CostClass.SMALL
    assert estimate(CostInputs(FormatKind.XLSX, None)).cls is CostClass.SMALL
    assert estimate(CostInputs(FormatKind.XLSX, None, cells=office_facts(_xlsx("A1:Z400000")).cells)).cls is not CostClass.SMALL


async def test_inspection_reads_office_facts_and_moves_a_sizeless_record_out_of_small(monkeypatch) -> None:
    from app.modules.parse_engine import wiring
    from app.modules.parse_engine.inspection import inspect_facts, wants_inspection

    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    assert wants_inspection("docx", None, b"x") and wants_inspection("xlsx", None, b"x")
    assert not wants_inspection("txt", None, b"x")
    monkeypatch.setenv("PARSE_ADMISSION", "legacy")
    assert not wants_inspection("docx", None, b"x")
    monkeypatch.setenv("PARSE_ADMISSION", "classes")

    docx = await inspect_facts(_docx(2000), extension="docx")
    xlsx = await inspect_facts(_xlsx("A1:Z400000"), extension="xlsx")
    assert (docx.pages, xlsx.cells) == (2000, 26 * 400_000)
    ctx = wiring.ClassContext(extension="docx")
    assert wiring.class_of(ctx, None) is CostClass.SMALL
    assert wiring.class_of(ctx.inspected(docx.pages), None) is not CostClass.SMALL
    ctx = wiring.ClassContext(extension="xlsx")
    assert wiring.class_of(ctx, None) is CostClass.SMALL
    assert wiring.class_of(ctx.inspected(cells=xlsx.cells), None) is not CostClass.SMALL
