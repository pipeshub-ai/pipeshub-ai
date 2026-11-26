"""FIX-PDFSKILL: a generated document's layout is checked before it is delivered.

A Codex job wrote a pdfkit report whose helper called
``doc.text(str, {x, y, width})``. pdfkit reads the position only as arguments, so every block flowed from the page's
top-left edge, and the engine checked only extracted text. ``fixtures/layout/ipl_broken.pdf`` is that PDF and
``ipl_broken.js`` the script; ``ipl_fixed.js`` is the same script with the position passed as arguments, and
``ipl_fixed.pdf`` what it makes in the Codex image.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from reportlab.pdfgen.canvas import Canvas

PACKS_ROOT = Path(__file__).resolve().parents[5] / "app" / "agents" / "agent_loop" / "skills" / "builtin_packs"
CHECKER = PACKS_ROOT / "office-utils" / "scripts" / "check_layout.py"
FIXTURES = Path(__file__).with_name("fixtures") / "layout"
DIGESTS = Path(__file__).with_name("fixtures") / "builtin_pack_digests.json"


def _checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_layout", CHECKER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_layout = _checker()


def _kinds(report) -> set[str]:
    return {fault.kind for fault in report.faults}


class TestThePdfFromTheReport:
    def test_the_ipl_pdf_is_flagged_for_text_at_the_page_edge_on_dark_panels_and_empty_cards(self, tmp_path: Path) -> None:
        report = check_layout.check(FIXTURES / "ipl_broken.pdf", tmp_path / "pages")

        assert report.pages == 5
        assert {"outside_margin", "low_contrast", "empty_box"} <= _kinds(report)
        edge = [f for f in report.faults if f.kind == "outside_margin"]
        assert {f.page for f in edge} == {1, 2, 3, 4, 5}
        assert all(f.bbox and f.bbox[0] < 1 for f in edge[:5])
        empty = {(f.page, round(f.bbox[0])) for f in report.faults if f.kind == "empty_box"}
        assert (1, 48) in empty and (2, 48) in empty

    def test_every_page_is_rendered_to_png(self, tmp_path: Path) -> None:
        report = check_layout.check(FIXTURES / "ipl_broken.pdf", tmp_path / "pages")

        assert [Path(p).name for p in report.rendered] == [f"ipl_broken-page-{n}.png" for n in range(1, 6)]
        assert all(Path(p).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for p in report.rendered)
        assert "rendered every page to PNG" in report.checked

    def test_the_same_script_with_the_position_as_arguments_is_clean(self, tmp_path: Path) -> None:
        script = (FIXTURES / "ipl_fixed.js").read_text()
        assert "doc.text(str, x, y, base);" in script and "{x, y, width" not in script

        report = check_layout.check(FIXTURES / "ipl_fixed.pdf", tmp_path / "pages")

        assert report.pages == 5 and len(report.rendered) == 5
        assert report.faults == []

    def test_the_command_exits_1_with_a_fix_for_each_kind_and_0_when_clean(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert check_layout.main([str(FIXTURES / "ipl_broken.pdf"), "--pages-dir", str(tmp_path / "a")]) == 1
        out = capsys.readouterr().out
        assert "doc.text(str, x, y, {width})" in out
        assert out.count("  fix: ") == len({line.split(":")[0].strip() for line in out.splitlines() if line.startswith("  ") and "fix:" not in line})

        assert check_layout.main([str(FIXTURES / "ipl_fixed.pdf"), "--pages-dir", str(tmp_path / "b")]) == 0
        assert "layout: clean" in capsys.readouterr().out

    def test_json_output_lists_faults_by_kind(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        check_layout.main([str(FIXTURES / "ipl_broken.pdf"), "--pages-dir", str(tmp_path), "--json"])
        data = json.loads(capsys.readouterr().out)
        assert data["pages"] == 5 and {"outside_margin", "low_contrast", "empty_box"} <= {f["kind"] for f in data["faults"]}

    def test_a_file_that_is_not_a_pdf_exits_2(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        assert check_layout.main([str(bad), "--pages-dir", str(tmp_path / "p")]) == 2


def _pdf(path: Path, draw: Callable[[Canvas, tuple[float, float]], None]) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    draw(c, A4)
    c.save()
    return path


class TestEachFaultKind:
    def test_text_over_other_text(self, tmp_path: Path) -> None:
        def draw(c: Canvas, size: tuple[float, float]) -> None:
            c.setFont("Helvetica", 14)
            c.drawString(72, 700, "Quarterly revenue rose sharply")
            c.drawString(80, 703, "Operating margin held steady")

        report = check_layout.check(_pdf(tmp_path / "o.pdf", draw), tmp_path / "p")
        assert _kinds(report) == {"text_overlap"}

    def test_text_covered_by_a_shape_drawn_after_it(self, tmp_path: Path) -> None:
        def draw(c: Canvas, size: tuple[float, float]) -> None:
            c.setFont("Helvetica", 12)
            c.setFillColorRGB(1, 1, 1)
            c.drawString(90, 700, "Hidden caption under the panel")
            c.setFillColorRGB(0.1, 0.2, 0.5)
            c.rect(72, 680, 300, 50, fill=1, stroke=0)

        report = check_layout.check(_pdf(tmp_path / "h.pdf", draw), tmp_path / "p")
        assert "hidden_text" in _kinds(report)

    def test_text_off_the_page_and_a_blank_page(self, tmp_path: Path) -> None:
        def draw(c: Canvas, size: tuple[float, float]) -> None:
            c.setFont("Helvetica", 12)
            c.drawString(size[0] - 40, 400, "This sentence runs past the right edge")
            c.showPage()
            c.showPage()

        report = check_layout.check(_pdf(tmp_path / "b.pdf", draw), tmp_path / "p")
        assert {"off_page", "blank_page"} <= _kinds(report)
        assert {f.page for f in report.faults if f.kind == "blank_page"} == {2}

    def test_a_platypus_report_with_a_banded_table_and_a_filled_panel_is_clean(self, tmp_path: Path) -> None:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        styles = getSampleStyleSheet()
        rows = [["Team", "Won", "Lost"]] + [[f"Team {i}", str(10 - i), str(4 + i)] for i in range(12)]
        table = Table(rows, colWidths=[200, 80, 80], repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B1725")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F1F5F6")]),
        ]))
        panel = Table([[Paragraph("<font color='white'>Champions: a second title, retained.</font>", styles["Normal"])]], colWidths=[450])
        panel.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#12263A")), ("TOPPADDING", (0, 0), (-1, -1), 12)]))
        path = tmp_path / "good.pdf"
        SimpleDocTemplate(str(path), pagesize=A4).build(
            [Paragraph("Season summary", styles["Title"]), panel, Spacer(1, 18), table, Paragraph("Source: league reports.", styles["Normal"])]
        )

        report = check_layout.check(path, tmp_path / "p")
        assert report.faults == []


class TestDecksAndDocuments:
    def test_a_deck_with_a_box_off_the_slide_and_text_boxes_on_each_other(self, tmp_path: Path) -> None:
        from pptx import Presentation
        from pptx.util import Inches

        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text_frame.text = "Revenue up 12%"
        slide.shapes.add_textbox(Inches(1.2), Inches(1.1), Inches(4), Inches(1)).text_frame.text = "Margin flat"
        slide.shapes.add_textbox(Inches(9), Inches(5), Inches(3), Inches(1)).text_frame.text = "Off the edge"
        deck.slides.add_slide(deck.slide_layouts[6])
        path = tmp_path / "bad.pptx"
        deck.save(str(path))

        report = check_layout.check(path)
        assert _kinds(report) == {"text_overlap", "off_page", "blank_page"}
        assert "not rendered: no renderer in this environment" in report.checked

    def test_a_tidy_deck_is_clean(self, tmp_path: Path) -> None:
        from pptx import Presentation
        from pptx.util import Inches

        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.shapes.add_textbox(Inches(0.5), Inches(0.5), Inches(9), Inches(1)).text_frame.text = "Season review"
        slide.shapes.add_textbox(Inches(0.5), Inches(2), Inches(9), Inches(3)).text_frame.text = "RCB retained the title."
        path = tmp_path / "good.pptx"
        deck.save(str(path))

        assert check_layout.check(path).faults == []

    def test_a_document_with_a_table_wider_than_the_page(self, tmp_path: Path) -> None:
        from docx import Document
        from docx.shared import Inches

        document = Document()
        document.add_paragraph("Results")
        table = document.add_table(rows=2, cols=3)
        for column in table.columns:
            column.width = Inches(3)
        path = tmp_path / "wide.docx"
        document.save(str(path))

        assert _kinds(check_layout.check(path)) == {"wide_table"}

    def test_a_document_that_fits_is_clean(self, tmp_path: Path) -> None:
        from docx import Document
        from docx.shared import Inches

        document = Document()
        document.add_paragraph("Results")
        table = document.add_table(rows=2, cols=3)
        for column in table.columns:
            column.width = Inches(2)
        path = tmp_path / "ok.docx"
        document.save(str(path))

        assert check_layout.check(path).faults == []


def _pack(name: str) -> str:
    return (PACKS_ROOT / name / "SKILL.md").read_text()


class TestThePacks:
    def test_the_pdf_pack_warns_of_the_pdfkit_position_pitfall_and_requires_the_check(self) -> None:
        body = _pack("pdf")
        assert "doc.text(str, x, y, { width })" in body
        assert "doc.text(str, { x, y, width })" in body
        assert "## Check before you deliver (required)" in body
        assert "skills/office-utils/scripts/check_layout.py report.pdf" in body
        assert "layout: clean" in body
        assert body.index("## Pitfalls") < body.index("## Generating a new PDF")

    def test_the_pdf_pack_requires_office_utils_so_the_loop_stages_the_checker_with_it(self) -> None:
        from app.agent_loop_lib.modules.providers.skills.loader import parse_skill_md

        assert "office-utils" in parse_skill_md(_pack("pdf"), expected_name="pdf").metadata.requires

    @pytest.mark.parametrize(("pack", "sample"), [("pptx", "deck.pptx"), ("docx", "report.docx")])
    def test_the_deck_and_document_packs_run_the_same_checker(self, pack: str, sample: str) -> None:
        body = _pack(pack)
        assert f"skills/office-utils/scripts/check_layout.py {sample}" in body
        assert "## Check before you deliver (required)" in body

    def test_the_checker_is_seeded_with_office_utils(self) -> None:
        from app.agents.agent_loop.skills.builtin_seeder import BuiltinSkillSeeder

        seeder = BuiltinSkillSeeder(str(PACKS_ROOT))
        office = next(s for s in seeder.packs if s.name == "office-utils")
        assert seeder.seeded_files(office)["scripts/check_layout.py"] == CHECKER.read_text()

    def test_the_checker_uses_only_libraries_every_sandbox_image_has(self) -> None:
        source = CHECKER.read_text()
        imported = {
            line.split()[1].split(".")[0]
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from ")) and not line.strip().startswith("from __future__")
        }
        assert imported <= {"argparse", "json", "sys", "typing", "dataclasses", "pathlib", "numpy", "pdfplumber", "pypdfium2", "pptx", "docx"}


def _digests() -> dict[str, tuple[str, str]]:
    """Each pack's version and the digest admission pins for it (ADR-SKL-01 D2)."""
    from app.agents.agent_loop.skills.builtin_seeder import BuiltinSkillSeeder
    from harness_core.skills import skill_bundle_digest, validate_skill_files

    seeder = BuiltinSkillSeeder(str(PACKS_ROOT))
    return {
        skill.name: (
            str(skill.metadata.pack_version),
            skill_bundle_digest(validate_skill_files({p: t.encode() for p, t in seeder.seeded_files(skill).items()})),
        )
        for skill in seeder.packs
    }


class TestPackVersions:
    """An org's seeded copy is upgraded only when a pack's version changes (``BuiltinSkillSeeder.sync``), so content
    changed under an old version never reaches an org already seeded, and its runs keep pinning the old bundle.
    ``fixtures/builtin_pack_digests.json`` records the digest of every released pack version; an entry is never
    rewritten. ``PXH_REGEN_PACK_DIGESTS=1`` adds the new versions."""

    def test_every_pack_version_keeps_the_content_it_was_released_with(self) -> None:
        recorded: dict[str, dict[str, str]] = json.loads(DIGESTS.read_text()) if DIGESTS.exists() else {}
        current = _digests()
        changed = [
            f"{name} {version}" for name, (version, digest) in current.items()
            if recorded.get(name, {}).get(version, digest) != digest
        ]
        assert changed == [], f"content changed without a new pack_version: {changed}"
        if os.environ.get("PXH_REGEN_PACK_DIGESTS") == "1":
            for name, (version, digest) in current.items():
                recorded.setdefault(name, {})[version] = digest
            DIGESTS.write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n")
        missing = [f"{name} {version}" for name, (version, _) in current.items() if version not in recorded.get(name, {})]
        assert missing == [], f"record the new pack versions with PXH_REGEN_PACK_DIGESTS=1: {missing}"

    def test_this_change_bumped_every_pack_it_touched(self) -> None:
        versions = {name: version for name, (version, _) in _digests().items()}
        assert versions["pdf"] == "1.4.1"
        assert versions["office-utils"] == "1.1.1"
        assert versions["docx"] == "1.2.0"
        assert versions["pptx"] == "1.3.0"
        assert versions["file-conversion"] == "1.2.0"
