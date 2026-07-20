---
name: pdf
description: "Use this skill whenever the user asks for a PDF — generating a new one from scratch (reports, invoices, letters), extracting text/tables from an existing PDF, or filling in an existing PDF form. Does not cover converting an existing Word/PowerPoint/Excel file to PDF (that requires LibreOffice, not available in this environment — see file-conversion)."
metadata:
  agent-loop:
    version: 1.0.0
    category: documents
    subcategory: pdf
    tags: [pdf, pdfkit, pdf-lib, reportlab, pypdf, pdfplumber]
    status: active
    source: builtin
    pack_name: pdf
    pack_version: 1.3.0
---

# PDF documents

## Quick Reference

| Task | Default (Node) | Fallback (Python) |
|------|-----------------|--------------------|
| Generate a new PDF from scratch | `pdfkit` — write a TypeScript program (packages: ["pdfkit"]) and run it via your coding tool; pre-installed in the standard sandbox image | `reportlab`'s `platypus` — reach for this for a report with several full pages of tabular data, where `platypus.Table`'s built-in pagination beats hand-rolling page breaks in `pdfkit` (see below) |
| Fill in an existing PDF form (AcroForm) | `pdf-lib` — TypeScript program (packages: ["pdf-lib"], pre-installed); `form.getTextField(name).setText(value)` | `pypdf` (`PdfWriter.update_page_form_field_values`) |
| Extract text from an existing PDF | — (no good Node equivalent) | `pdfplumber` (also gets tables) or `pypdf` (faster, text-only) |
| Extract tables from an existing PDF | — (no good Node equivalent) | `pdfplumber` (`page.extract_tables()`) |

All of the above are already installed/allowlisted — no extra setup either way. **Default to `pdfkit`/`pdf-lib` in TypeScript for generation and form-filling** per this environment's Node-first policy for office-document creation; extraction has no solid Node option here, so stays Python.

## Generating a new PDF (pdfkit)

`pdfkit` is a low-level, stream-based PDF library — it does not have `reportlab.platypus`'s automatic page-flow layout for tables, so you place text yourself and check the vertical position before every element:

```typescript
import PDFDocument from "pdfkit";
import fs from "fs";

const doc = new PDFDocument({ size: "A4", margins: { top: 50, bottom: 50, left: 50, right: 50 } });
doc.pipe(fs.createWriteStream("report.pdf"));

doc.fontSize(18).text("Invoice", { align: "center" });
doc.moveDown();

// doc.text() without an explicit x/y auto-wraps and flows below the previous
// element, including inserting new pages for long paragraphs — only switch
// to explicit x/y positioning (below) for tabular/columnar layout.
doc.fontSize(11).text("Body text wraps and paginates automatically here.");

doc.end();
```

- Always set `size` explicitly (`"A4"` or `"LETTER"`) rather than relying on the default, since it may not match the user's locale expectation.
- Plain flowing text (paragraphs, letters) needs no manual layout — `doc.text()` alone handles wrapping and page breaks. Only tabular/columnar content needs the manual pattern below.
- For tabular data (an invoice's line items, a report's data table), `pdfkit` has no built-in table primitive — draw it with fixed per-column `x` offsets and a manual page-break check before each row:

```typescript
const startX = 50;
const colWidths = { desc: 260, qty: 60, total: 90 };
let y = doc.y;

for (const item of items) {
  if (y > doc.page.height - doc.page.margins.bottom - 20) {
    doc.addPage();
    y = doc.page.margins.top;
  }
  doc.text(item.desc, startX, y, { width: colWidths.desc });
  doc.text(String(item.qty), startX + colWidths.desc, y, { width: colWidths.qty, align: "right" });
  doc.text(item.total.toFixed(2), startX + colWidths.desc + colWidths.qty, y, { width: colWidths.total, align: "right" });
  y += 20; // row height — bump if a cell's wrapped text needs more than one line
}
```

- If a single row's tallest cell can wrap to multiple lines, compute the row height from `doc.heightOfString(text, { width })` across that row's cells before advancing `y`, rather than a fixed row height — otherwise wrapped rows overlap the next row.
- Reach for `reportlab.platypus.Table` (Python) instead when the table is long enough that hand-rolling pagination is more work than it's worth — it paginates a table across pages automatically given column widths and a `TableStyle`.

### Wide tables (6+ columns) — preventing overflow

Tables with many columns (ticket lists, dashboards, audit logs) overflow if you use portrait orientation or eyeball column widths. Follow this checklist:

1. **Switch to landscape** when column count ≥ 6:
   ```typescript
   const doc = new PDFDocument({ size: "A4", layout: "landscape", margins: { top: 40, bottom: 40, left: 40, right: 40 } });
   ```
2. **Compute column widths from available page width**, never hardcode pixel values:
   ```typescript
   const pageWidth = doc.page.width - doc.page.margins.left - doc.page.margins.right;
   // Give variable-length columns (e.g. "Summary") a proportionally larger share
   const fixedColWidth = 70; // narrow cols: status, priority, type, etc.
   const fixedCols = 6;
   const flexColWidth = pageWidth - fixedCols * fixedColWidth; // remainder for "Summary"
   ```
3. **Reduce font size** to 8–9pt for dense tables (many rows and columns); header can stay 9–10pt bold.
4. **Truncate long cell text** rather than letting it overflow into the next column:
   ```typescript
   function truncate(text: string, font: string, size: number, maxWidth: number): string {
     while (doc.widthOfString(text, { font, size }) > maxWidth && text.length > 0) {
       text = text.slice(0, -1);
     }
     return text.length < original.length ? text + "…" : text;
   }
   ```
   Or use `{ width: colWidth, ellipsis: true }` in the `.text()` call (pdfkit supports this natively).
5. **Wrap within cells** as a fallback — pass `{ width: colWidth }` and compute row height with `doc.heightOfString(cellText, { width: colWidth })` across all cells in the row, then use the tallest.

### Wide tables — reportlab (Python, preferred for 20+ row tables)

`reportlab.platypus.Table` handles both pagination and cell wrapping automatically when you give it explicit `colWidths` that sum to the available page width:

```python
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT

pagesize = landscape(A4)
doc = SimpleDocTemplate("report.pdf", pagesize=pagesize,
                        topMargin=15*mm, bottomMargin=15*mm,
                        leftMargin=15*mm, rightMargin=15*mm)
available_width = pagesize[0] - 30*mm  # left + right margins

# Assign proportional widths — flex column gets remainder
fixed = {"key": 45*mm, "type": 30*mm, "status": 30*mm,
         "priority": 28*mm, "due": 28*mm, "sprint": 35*mm}
flex_width = available_width - sum(fixed.values())  # "Summary" gets the rest

col_widths = [fixed["key"], flex_width, fixed["type"], fixed["status"],
              fixed["priority"], fixed["due"], fixed["sprint"]]

# Wrap long text in Paragraph objects (plain strings in Table cells do NOT wrap)
styles = getSampleStyleSheet()
cell_style = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=8, leading=10)

table_data = [["Key", "Summary", "Type", "Status", "Priority", "Due", "Sprint"]]
for ticket in tickets:
    table_data.append([
        ticket["key"],
        Paragraph(ticket["summary"], cell_style),  # wraps within cell
        ticket["type"],
        ticket["status"],
        ticket["priority"],
        ticket.get("due", "—"),
        ticket.get("sprint", "—"),
    ])

table = Table(table_data, colWidths=col_widths, repeatRows=1)
table.setStyle(TableStyle([
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, 0), 9),
    ("FONTSIZE", (0, 1), (-1, -1), 8),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.Color(0.95, 0.95, 0.95)]),
    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
]))
doc.build([table])
```

Key points:
- **`landscape(A4)`** — always use landscape for 6+ column tables.
- **`colWidths` must sum to `available_width`** — if they exceed it, the table overflows the page; if they're omitted, reportlab auto-sizes but often overflows.
- **Wrap long text in `Paragraph(..., style)`** — plain strings inside a `Table` cell will NOT wrap; they clip or overflow. This is the #1 cause of reportlab table overflow.
- **`repeatRows=1`** — repeats the header row on every page when the table spans multiple pages.
- **Font size 8–9pt** for body, 9–10pt for header — standard for dense reports.

## Filling in a form (pdf-lib)

```typescript
import { PDFDocument } from "pdf-lib";
import fs from "fs";

const pdfDoc = await PDFDocument.load(fs.readFileSync("form.pdf"));
const form = pdfDoc.getForm();
for (const field of form.getFields()) console.log(field.getName(), field.constructor.name); // discover field names/types first

form.getTextField("first_name").setText("Jane");
fs.writeFileSync("filled.pdf", await pdfDoc.save());
```

Field names are exactly what the form's original author named them (often generic like `Text1`) — always call `form.getFields()` first rather than guessing. `pdf-lib` is also the right tool for merging/splitting existing PDFs (`PDFDocument.copyPages`) if that comes up alongside form-filling.

## Extracting content (Python — no solid Node equivalent for this)

- `pdfplumber`: `page.extract_text()` for reading order-preserving text, `page.extract_tables()` for tabular data — the better default for anything that might contain a table, since `pypdf`'s text extraction does not attempt to reconstruct table structure.
- `pypdf`: faster and sufficient when you only need plain text and know the source has no tables (e.g. extracting a paragraph from a text-heavy report).
- Scanned (image-only) PDFs have no extractable text via either library — check `page.extract_text()` for an empty/near-empty result and tell the user OCR would be needed (not available in this environment) rather than returning an empty string silently.

## Not supported in this environment

- Converting an existing `.docx`/`.pptx`/`.xlsx` to PDF requires a rendering engine (LibreOffice) not installed in the sandbox yet.
