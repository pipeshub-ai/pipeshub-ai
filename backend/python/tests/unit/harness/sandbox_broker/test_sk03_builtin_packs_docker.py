"""SK-03 (C3.54, WP-13): PipesHub's built-in pdf, docx, xlsx, pptx and data-analysis packs run in every engine's run
image.

Each release engine's run image (the allowlist's ``image``: the tools layer the native engines share, and Gemini
CLI's) under the real Docker broker in hardened mode (read-only root, no capabilities, no network). The built-in
packs (``app/agents/agent_loop/skills/builtin_packs``) are seeded where a run's skills are mounted (``/skills``,
read-only), and their own scripts and the Python paths they name run with the image's packages only; what they
make lands in the workspace's ``out/``, where the controller publishes deliverables from. Skipped without a daemon
or an image (each engine's ``deployment/sandbox-images/<engine>/build.sh``).

SKILLPACKS: each pack's preferred path runs too: data-analysis's scipy; the docx, xlsx and pptx packs' Node programs
(``docx``, ``exceljs``, ``pptxgenjs``, in TypeScript with ``import`` as the packs show them); the pdf pack's ``pdfkit``
generation, ``pdf-lib`` form filling, reportlab table pagination and pdfplumber/pypdf extraction. Python readers
open what Node wrote.
"""

from __future__ import annotations

import io
import tarfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.ids import RandomIds
from harness_core.sandbox.tar import put_files
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.skills.materializer import skills_mount
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import run
from harness_core.workspace.sync.archive import Owner
from harness_engines.release import release_allowlist
from tests.unit.harness.sandbox_broker.conftest import cleanup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.sandbox.ports import SandboxHandle

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]

PACKS_ROOT = Path(__file__).resolve().parents[4] / "app" / "agents" / "agent_loop" / "skills" / "builtin_packs"
PACKS = ("docx", "xlsx", "data-analysis", "office-utils", "pdf", "pptx")
SKILLS = "/skills"
LEASE = LeaseRef("worker-sk03", 1)
# One run image per engine version; versions sharing an image are one case.
IMAGES = sorted({r.image for r in release_allowlist()})
SALES = b"region,quarter,amount\nnorth,Q1,120.5\nnorth,Q2,99.0\nsouth,Q1,80.25\nsouth,Q2,101.0\n"


def _present(image: str) -> bool:
    try:
        import docker

        client = docker.from_env()
        client.images.get(image)
        client.close()
    except Exception:
        return False
    return True


def _packs_tar() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for pack in PACKS:
            for path in sorted((PACKS_ROOT / pack).rglob("*")):
                if path.is_file():
                    archive.add(path, arcname=path.relative_to(PACKS_ROOT).as_posix(), recursive=False)
    return buffer.getvalue()


async def _one(data: bytes) -> AsyncIterator[bytes]:
    yield data


@pytest.fixture(params=IMAGES)
async def sandbox(request: pytest.FixtureRequest) -> AsyncIterator[SandboxHandle]:
    image: str = request.param
    if not _present(image):
        pytest.skip(f"needs Docker and {image}")
    provider = DockerClientProvider(max_workers=8)
    namespace = f"io.pipeshub.sk03.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-sk03-" + namespace.rsplit(".", 1)[1]
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=ManualClock(), ids=RandomIds(), allowed_images=frozenset({image}),
        label_namespace=namespace, volume_prefix=prefix,
    )
    spec = SandboxSpec(
        image_digest=image,
        mode=RuntimeMode.DOCKER_HARDENED,
        resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=100),
        mounts=(MountSpec("/workspace"), MountSpec("/state"), skills_mount(SKILLS)),
        labels={"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "SK03"},
    )
    handle = await broker.acquire(spec, lease=LEASE, idempotency_key=f"sk03-{uuid.uuid4().hex}")
    try:
        await handle.seed_tar(SKILLS, _one(_packs_tar()))
        await put_files(handle, {"/workspace/in/sales.csv": SALES, "/workspace/out/.keep": b""}, owner=Owner())
        yield handle
    finally:
        await broker.release(handle)
        await provider.run_blocking(cleanup, provider, namespace, prefix)
        provider.close()


async def _sh(handle: SandboxHandle, script: str) -> tuple[int, str]:
    out, err, code = await run(await handle.exec(["/bin/sh", "-c", f"cd /workspace && {script}"], env={}), timeout_s=120)
    return code, (out + err).decode("utf-8", "replace").strip()


async def _ok(handle: SandboxHandle, script: str) -> str:
    code, out = await _sh(handle, script)
    assert code == 0, (script, out)
    return out


async def test_sk03_the_packs_are_mounted_read_only_where_engines_read_skills(sandbox: SandboxHandle) -> None:
    listed = await _ok(sandbox, f"ls {SKILLS}")
    assert set(listed.split()) == set(PACKS)
    code, _ = await _sh(sandbox, f"touch {SKILLS}/docx/x")
    assert code != 0


async def test_sk03_data_analysis_writes_its_result_and_chart_to_outputs(sandbox: SandboxHandle) -> None:
    script = (
        "import pandas as pd, matplotlib.pyplot as plt\n"
        "df = pd.read_csv('in/sales.csv', dtype={'region': str, 'quarter': str, 'amount': float})\n"
        "assert df.isnull().sum().sum() == 0 and df.duplicated().sum() == 0\n"
        "totals = df.groupby('region', as_index=False)['amount'].sum()\n"
        "totals.to_csv('out/totals.csv', index=False)\n"
        "totals.plot.bar(x='region', y='amount'); plt.savefig('out/totals.png')\n"
    )
    await _ok(sandbox, f"python3 - <<'PY'\n{script}PY")
    assert await _ok(sandbox, "cat out/totals.csv") == "region,amount\nnorth,219.5\nsouth,181.25"
    assert (await _ok(sandbox, "head -c 4 out/totals.png | od -An -c")).split()[1:4] == ["P", "N", "G"]


async def test_sk03_xlsx_writes_a_workbook_and_its_formula_check_runs(sandbox: SandboxHandle) -> None:
    script = (
        "from openpyxl import Workbook\n"
        "wb = Workbook(); ws = wb.active; ws.append(['amount']); ws.append([1]); ws.append([2])\n"
        "ws['A4'] = '=SUM(A2:A3)'; wb.save('out/book.xlsx')\n"
        "bad = Workbook(); bad.active['A1'] = 5; bad.active['A2'] = '=A1/B9'; bad.save('bad.xlsx')\n"
    )
    await _ok(sandbox, f"python3 - <<'PY'\n{script}PY")
    await _ok(sandbox, f"python3 {SKILLS}/xlsx/scripts/verify_formulas.py out/book.xlsx")
    code, out = await _sh(sandbox, f"python3 {SKILLS}/xlsx/scripts/verify_formulas.py bad.xlsx")
    assert code == 1 and "divides by B9" in out


async def test_sk03_docx_is_edited_through_office_utils_and_reads_back(sandbox: SandboxHandle) -> None:
    script = (
        "from docx import Document\n"
        "d = Document(); d.add_heading('Quarterly report', 1); d.add_paragraph('Draft total: TBD'); d.save('draft.docx')\n"
    )
    await _ok(sandbox, f"python3 - <<'PY'\n{script}PY")
    await _ok(sandbox, f"python3 {SKILLS}/office-utils/scripts/unpack.py draft.docx unpacked/")
    await _ok(sandbox, "sed -i 's/Draft total: TBD/Total: 400.75/' unpacked/word/document.xml")
    await _ok(sandbox, f"python3 {SKILLS}/office-utils/scripts/pack.py unpacked/ out/report.docx")
    read = "from docx import Document; print([p.text for p in Document('out/report.docx').paragraphs])"
    assert await _ok(sandbox, f'python3 -c "{read}"') == "['Quarterly report', 'Total: 400.75']"


async def test_sk03_data_analysis_runs_scipy_statistics(sandbox: SandboxHandle) -> None:
    script = (
        "import pandas as pd\n"
        "from scipy import stats\n"
        "df = pd.read_csv('in/sales.csv')\n"
        "fit = stats.linregress([1, 2, 3, 4], df['amount'])\n"
        "north, south = (df[df.region == r]['amount'] for r in ('north', 'south'))\n"
        "print(f'{fit.slope:.3f} {stats.ttest_ind(north, south).pvalue:.3f}')\n"
    )
    assert await _ok(sandbox, f"python3 - <<'PY'\n{script}PY") == "-7.725 0.329"


async def test_sk03_docx_pack_writes_a_document_with_node_docx(sandbox: SandboxHandle) -> None:
    program = (
        'import { Document, HeadingLevel, Packer, Paragraph, TextRun } from "docx";\n'
        'import { writeFileSync } from "node:fs";\n'
        "const total: number = 400.75;\n"
        "const doc = new Document({ sections: [{ children: [\n"
        '  new Paragraph({ text: "Quarterly report", heading: HeadingLevel.HEADING_1 }),\n'
        "  new Paragraph({ children: [new TextRun({ text: `Total: ${total}`, bold: true })] }),\n"
        "] }] });\n"
        'writeFileSync("out/report.docx", await Packer.toBuffer(doc));\n'
    )
    await _ok(sandbox, f"cat > report.ts <<'TS'\n{program}TS\nnode report.ts")
    read = "from docx import Document; print([p.text for p in Document('out/report.docx').paragraphs])"
    assert await _ok(sandbox, f'python3 -c "{read}"') == "['Quarterly report', 'Total: 400.75']"


async def test_sk03_xlsx_pack_writes_a_workbook_with_exceljs_and_its_ts_check_runs(sandbox: SandboxHandle) -> None:
    program = (
        'import ExcelJS from "exceljs";\n'
        "const wb = new ExcelJS.Workbook();\n"
        'const ws = wb.addWorksheet("Sales");\n'
        'ws.addRows([["amount"], [1], [2]]);\n'
        'ws.getCell("A4").value = { formula: "SUM(A2:A3)", result: 3 };\n'
        'await wb.xlsx.writeFile("out/book.xlsx");\n'
        'const bad = new ExcelJS.Workbook();\n'
        'const sheet = bad.addWorksheet("S");\n'
        'sheet.getCell("A1").value = 5;\n'
        'sheet.getCell("A2").value = { formula: "A1/B9" };\n'
        'await bad.xlsx.writeFile("bad.xlsx");\n'
    )
    await _ok(sandbox, f"cat > book.ts <<'TS'\n{program}TS\nnode book.ts")
    read = "import openpyxl; ws = openpyxl.load_workbook('out/book.xlsx').active; print(ws.title, ws['A3'].value, ws['A4'].value)"
    assert await _ok(sandbox, f'python3 -c "{read}"') == "Sales 2 =SUM(A2:A3)"
    await _ok(sandbox, f"node {SKILLS}/xlsx/scripts/verify_formulas.ts out/book.xlsx")
    code, out = await _sh(sandbox, f"node {SKILLS}/xlsx/scripts/verify_formulas.ts bad.xlsx")
    assert code == 1 and "B9" in out, out


async def test_sk03_pdf_pack_generates_with_pdfkit_and_extracts_with_pdfplumber_and_pypdf(sandbox: SandboxHandle) -> None:
    program = (
        'import PDFDocument from "pdfkit";\n'
        'import fs from "fs";\n'
        'const doc = new PDFDocument({ size: "A4", margins: { top: 50, bottom: 50, left: 50, right: 50 } });\n'
        'const done = new Promise((resolve) => doc.pipe(fs.createWriteStream("out/report.pdf")).on("finish", resolve));\n'
        'doc.fontSize(18).text("Invoice 42", { align: "center" });\n'
        "doc.moveDown();\n"
        'const total: number = 400.75;\n'
        "doc.fontSize(11).text(`Total due: ${total}`);\n"
        "doc.end();\n"
        "await done;\n"
    )
    await _ok(sandbox, f"cat > report.ts <<'TS'\n{program}TS\nnode report.ts")
    read = (
        "import pdfplumber, pypdf\n"
        "with pdfplumber.open('out/report.pdf') as pdf: print(pdf.pages[0].extract_text().splitlines())\n"
        "print(pypdf.PdfReader('out/report.pdf').pages[0].extract_text().split())\n"
    )
    assert await _ok(sandbox, f"python3 - <<'PY'\n{read}PY") == (
        "['Invoice 42', 'Total due: 400.75']\n['Invoice', '42', 'Total', 'due:', '400.75']"
    )


async def test_sk03_pdf_pack_fills_a_form_with_pdf_lib(sandbox: SandboxHandle) -> None:
    make = (
        'import { PDFDocument } from "pdf-lib";\n'
        'import fs from "fs";\n'
        "const blank = await PDFDocument.create();\n"
        'blank.getForm().createTextField("first_name").addToPage(blank.addPage(), { x: 50, y: 700 });\n'
        'fs.writeFileSync("form.pdf", await blank.save());\n'
    )
    fill = (
        'import { PDFDocument } from "pdf-lib";\n'
        'import fs from "fs";\n'
        'const pdfDoc = await PDFDocument.load(fs.readFileSync("form.pdf"));\n'
        "const form = pdfDoc.getForm();\n"
        "for (const field of form.getFields()) console.log(field.getName(), field.constructor.name);\n"
        'form.getTextField("first_name").setText("Jane");\n'
        'fs.writeFileSync("out/filled.pdf", await pdfDoc.save());\n'
    )
    await _ok(sandbox, f"cat > make.ts <<'TS'\n{make}TS\nnode make.ts")
    assert await _ok(sandbox, f"cat > fill.ts <<'TS'\n{fill}TS\nnode fill.ts") == "first_name PDFTextField"
    read = "import pypdf; print(pypdf.PdfReader('out/filled.pdf').get_fields()['first_name'].get('/V'))"
    assert await _ok(sandbox, f'python3 -c "{read}"') == "Jane"


async def test_sk03_pdf_pack_paginates_a_table_with_reportlab_and_pdfplumber_reads_it(sandbox: SandboxHandle) -> None:
    script = (
        "from reportlab.lib import colors\n"
        "from reportlab.lib.pagesizes import A4\n"
        "from reportlab.platypus import SimpleDocTemplate, Table, TableStyle\n"
        "rows = [['region', 'amount']] + [[f'r{i}', str(i)] for i in range(80)]\n"
        "table = Table(rows, colWidths=[200, 200], repeatRows=1)\n"
        "table.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.5, colors.grey)]))\n"
        "SimpleDocTemplate('out/table.pdf', pagesize=A4).build([table])\n"
        "import pdfplumber\n"
        "with pdfplumber.open('out/table.pdf') as pdf:\n"
        "    tables = [t for page in pdf.pages for t in page.extract_tables()]\n"
        "    print(len(pdf.pages), tables[0][0], tables[0][1], tables[-1][-1])\n"
    )
    assert await _ok(sandbox, f"python3 - <<'PY'\n{script}PY") == "3 ['region', 'amount'] ['r0', '0'] ['r79', '79']"


async def test_sk03_pptx_pack_creates_a_deck_with_pptxgenjs_and_python_pptx_reads_it(sandbox: SandboxHandle) -> None:
    program = (
        'import PptxGenJS from "pptxgenjs";\n'
        "function text(value: string): string {\n"
        '  if (value.includes("\\\\n")) throw new Error(`literal backslash-n: ${value}`);\n'
        "  return value;\n"
        "}\n"
        "const pptx = new PptxGenJS();\n"
        'pptx.defineLayout({ name: "WIDE", width: 13.33, height: 7.5 });\n'
        'pptx.layout = "WIDE";\n'
        "const title = pptx.addSlide();\n"
        'title.background = { color: "1B2A4A" };\n'
        'title.addText(text("Q3 review"), { x: 0.8, y: 2.6, w: 11.7, h: 1.2, fontSize: 40, bold: true, color: "FFFFFF" });\n'
        "const slide = pptx.addSlide();\n"
        'slide.addShape("rect", { x: 0, y: 0, w: 13.33, h: 0.9, fill: { color: "1B2A4A" } });\n'
        'slide.addText(text("Key findings"), { x: 0.5, y: 0.12, w: 9, h: 0.66, fontSize: 24, bold: true });\n'
        'await pptx.writeFile({ fileName: "out/deck.pptx" });\n'
    )
    await _ok(sandbox, f"cat > deck.ts <<'TS'\n{program}TS\nnode deck.ts")
    read = (
        "from pptx import Presentation\n"
        "deck = Presentation('out/deck.pptx')\n"
        "print(len(deck.slides), round(deck.slide_width / 914400, 2),"
        " [s.text_frame.text for slide in deck.slides for s in slide.shapes if s.has_text_frame and s.text_frame.text])\n"
    )
    assert await _ok(sandbox, f"python3 - <<'PY'\n{read}PY") == "2 13.33 ['Q3 review', 'Key findings']"
    await _ok(sandbox, f"python3 {SKILLS}/office-utils/scripts/unpack.py out/deck.pptx unpacked/")
    assert "ppt/slides/slide2.xml" in await _ok(sandbox, "cd unpacked && find ppt/slides -name '*.xml' | sort")
