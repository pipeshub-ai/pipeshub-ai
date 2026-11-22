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

    parsed: list[bool] = []
    real = zf.ZipFile._RealGetContents

    def spy(self: zf.ZipFile) -> None:
        parsed.append(True)  # recorded, not raised: office_facts swallows every exception by design
        real(self)

    monkeypatch.setattr(zf.ZipFile, "_RealGetContents", spy)
    assert office_facts(_zip({f"m{i}": b"" for i in range(5001)})) == OfficeFacts()
    assert parsed == []


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


def test_self_reported_office_metadata_never_lowers_a_size_based_class() -> None:
    """python-docx keeps a template's Pages=1, openpyxl can write A1: neither makes a 150 MiB file small."""
    big = 150 * MIB
    assert estimate(CostInputs(FormatKind.DOCX, big)).cls is not CostClass.SMALL  # the size prior: large
    assert estimate(CostInputs(FormatKind.DOCX, big, pages=1)).cls is not CostClass.SMALL
    assert estimate(CostInputs(FormatKind.PPTX, big, pages=1)).cls is not CostClass.SMALL
    assert estimate(CostInputs(FormatKind.XLSX, big)).cls is not CostClass.SMALL
    assert estimate(CostInputs(FormatKind.XLSX, big, cells=1)).cls is not CostClass.SMALL
    # ... while the facts still raise a class the size alone would not
    assert estimate(CostInputs(FormatKind.DOCX, MIB, pages=5000)).cls is not CostClass.SMALL
    assert estimate(CostInputs(FormatKind.XLSX, MIB, cells=10**9)).cls is not CostClass.SMALL
    # a small file with small facts stays small
    assert estimate(CostInputs(FormatKind.DOCX, MIB, pages=3)).cls is CostClass.SMALL


def _with_members(pages: int, extra: int) -> bytes:
    members = {"docProps/app.xml": f"<Properties><Pages>{pages}</Pages></Properties>".encode()}
    members.update({f"m{i}": b"" for i in range(extra)})
    return _zip(members)


def test_the_member_cap_is_five_thousand_entries() -> None:
    assert office_facts(_with_members(2000, 4999)) == OfficeFacts(pages=2000)  # 5000 entries in all: read
    assert office_facts(_with_members(2000, 5000)) == OfficeFacts()  # 5001: refused, though it is a sound package


def test_a_directory_that_declares_one_entry_but_is_huge_is_refused_before_it_is_parsed(monkeypatch) -> None:
    import zipfile as zf

    data = bytearray(_with_members(2000, 30000))
    eocd = bytes(data).rfind(b"PK\x05\x06")
    data[eocd + 10:eocd + 12] = (1).to_bytes(2, "little")  # the declared count lies
    data[eocd + 8:eocd + 10] = (1).to_bytes(2, "little")
    assert int.from_bytes(data[eocd + 12:eocd + 16], "little") > (1 << 20) // 4  # a real directory of tens of thousands of entries

    parsed: list[bool] = []
    real = zf.ZipFile._RealGetContents

    def spy(self: zf.ZipFile) -> None:
        parsed.append(True)  # recorded, not raised: office_facts swallows every exception by design
        real(self)

    monkeypatch.setattr(zf.ZipFile, "_RealGetContents", spy)
    # the size bound is 1 MiB: pad the directory size field to exceed it the way a crafted package does
    data[eocd + 12:eocd + 16] = ((1 << 20) + 1).to_bytes(4, "little")
    assert office_facts(bytes(data)) == OfficeFacts()
    assert parsed == []  # refused by the directory-size bound, before one entry was parsed


def test_an_xlsx_cell_area_reaches_the_class_through_the_event_and_the_consumers_context() -> None:
    """From the inspection's facts to the event data and into the class context the consumers rebuild."""
    from app.modules.parse_engine import wiring
    from app.services.messaging.config import PipelineEventData

    cells = office_facts(_xlsx("A1:Z400000")).cells
    data = PipelineEventData(record_id="r", cells=cells)
    assert data.cells == cells
    ctx = wiring.ClassContext(extension="xlsx").inspected(data.pages, scanned=data.scanned, cells=data.cells)
    assert ctx.cells == cells and ctx.facts() == {"cells": cells}
    assert wiring.class_of(ctx, 2 * MIB) is not CostClass.SMALL  # 10.4 million cells in a 2 MiB file: not small


def test_metadata_from_an_earlier_revision_never_lowers_the_class_either() -> None:
    from app.modules.parse_engine.cost import CostHint

    big = 200 * MIB
    docx = estimate(CostInputs(FormatKind.DOCX, big, hint=CostHint(pages=1)))
    assert docx.cls is not CostClass.SMALL, docx
    xlsx = estimate(CostInputs(FormatKind.XLSX, big, hint=CostHint(cells=1)))
    assert xlsx.cls is not CostClass.SMALL, xlsx
    # a hint that agrees with a small file keeps it small
    assert estimate(CostInputs(FormatKind.DOCX, MIB, hint=CostHint(pages=3))).cls is CostClass.SMALL
