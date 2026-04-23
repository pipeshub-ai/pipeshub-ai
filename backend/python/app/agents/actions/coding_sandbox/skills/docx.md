# DOCX Design Skill

Use this skill **before writing any code** that produces a `.docx` file —
report, memo, letter, proposal, brief, audit. A `Document()` with a bunch of
`add_paragraph` calls and no styles produces a wall of Times New Roman that
looks unreviewed. Do better.

---

## Quick reference — pick the right runtime

| Task                            | Runtime               | Library         |
|---------------------------------|-----------------------|-----------------|
| New document from scratch       | `execute_typescript`  | `pipeshub-docs` (preferred) or `docx` |
| Edit existing `.docx` template  | `execute_python`      | `python-docx`   |
| Read / extract content          | `execute_python`      | `python-docx` or `pandoc` |
| Render document for visual QA   | `coding_sandbox.render_artifact_preview` | LibreOffice + pdftoppm |

The Node path (`pipeshub-docs` on top of `docx-js`) is the default for new
documents. Its Table / TOC / header-footer APIs are significantly better than
`python-docx`. Use `python-docx` only for template editing.

---

## Step 1: Set up the page

- **Always set page size explicitly.** `docx-js` defaults to A4; US documents
  need US Letter. The helper library handles this, but if you drop to
  `docx-js` directly, 1 inch = 1440 DXA.
- **Margins:** 1" all around (1440 DXA) is the safe default. Reduce to 0.75"
  only for high-density reports. Never under 0.5".
- **Gutter + mirror margins** for printed booklets; skip them for anything
  viewed on screen.

```ts
// docx-js page setup
page: {
  size:   { width: 12240, height: 15840 },  // US Letter
  margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 },
}
```

| Paper      | Width DXA | Height DXA | Content width @ 1" margins |
|------------|-----------|------------|----------------------------|
| US Letter  | 12240     | 15840      | 9360                       |
| A4         | 11906     | 16838      | 9026                       |

---

## Step 2: Type system

Pick one pair, apply it document-wide, commit to it.

| Headers        | Body              | Vibe        |
|----------------|-------------------|-------------|
| Calibri        | Calibri           | Safe default |
| Cambria        | Calibri           | Corporate   |
| Georgia        | Georgia           | Editorial   |
| Arial Black    | Arial             | Bold        |
| Palatino       | Palatino          | Literary    |

| Element     | Size / weight           |
|-------------|-------------------------|
| Title       | 32 pt bold, centered, space after 480 DXA |
| Heading 1   | 24 pt bold, before 360 / after 240 DXA    |
| Heading 2   | 18 pt bold, before 240 / after 180 DXA    |
| Heading 3   | 14 pt bold, before 180 / after 120 DXA    |
| Body        | 11–12 pt, 1.15 line height, justified OFF  |
| Caption     | 9–10 pt italic, muted gray `595959`        |
| Footer      | 9 pt muted                                 |

**Override the built-in heading styles** by using their exact IDs
(`Heading1`, `Heading2`, ...). Custom style names do not show up in a Word
table of contents. Always set `outlineLevel` on heading styles (0 for H1,
1 for H2...) or the TOC will be empty.

---

## Step 3: Structural elements every report deserves

- **Cover page** — title, subtitle, author, date. Use tab stops (RIGHT +
  `TabStopPosition.MAX`) to right-align the date on the same line as the
  company name.
- **Table of contents** — use the library's `TableOfContents` element. It
  only picks up paragraphs styled with `HeadingLevel.HEADING_1..6`; custom
  styles break it.
- **Headers & footers** — page numbers in the footer, section / document
  title in the header. Use a left-border on the header paragraph for a
  subtle brand mark instead of a horizontal rule.
- **Section breaks** — every major section (Executive Summary, Findings,
  Recommendations) starts on a new page with `pageBreakBefore: true`.
- **Pull quotes / callouts** — shaded 1-row 1-col table or a paragraph with
  a left border and indent. Break up walls of text.

---

## Step 4: Tables (get this right or they look broken)

`docx-js` tables require **dual widths** — both the table and every cell
must declare their width in DXA. Percentages break in Google Docs; never use
them.

```ts
import { Table, TableRow, TableCell, Paragraph, TextRun,
         WidthType, BorderStyle, ShadingType } from "docx";

const TABLE_W = 9360;                         // full content width
const COL_WIDTHS = [3360, 3000, 3000];        // must sum to TABLE_W
const borderColor = "D0D7DE";
const border = { style: BorderStyle.SINGLE, size: 1, color: borderColor };
const borders = { top: border, bottom: border, left: border, right: border };

new Table({
  width: { size: TABLE_W, type: WidthType.DXA },
  columnWidths: COL_WIDTHS,
  rows: [
    new TableRow({
      tableHeader: true,
      children: COL_WIDTHS.map((w, i) =>
        new TableCell({
          width: { size: w, type: WidthType.DXA },
          shading: { fill: "1E2761", type: ShadingType.CLEAR },   // CLEAR, not SOLID
          borders,
          margins: { top: 80, bottom: 80, left: 120, right: 120 },
          children: [new Paragraph({
            children: [new TextRun({ text: ["Metric", "Q3", "Q4"][i],
                                     bold: true, color: "FFFFFF" })],
          })],
        }),
      ),
    }),
    // data rows — alternate fill "FFFFFF" / "F6F8FA" for readability
  ],
})
```

Table rules:

- **Use `WidthType.DXA`** everywhere. Never `WidthType.PERCENTAGE`.
- **Table width = sum of `columnWidths`** exactly.
- **Every cell sets its own `width`** matching the corresponding column.
- **Cell margins are internal padding** — 80 top/bottom, 120 left/right is
  the readable default.
- **`ShadingType.CLEAR`** for shading. `SOLID` paints the cell black in
  some renderers.
- **Alternate row shading** (`FFFFFF` / `F6F8FA`) for readability.
- **Never use a 1-row 1-col table as a divider.** Cells have a minimum
  rendered height and leave an empty box. Use a paragraph border instead.

---

## Step 5: Lists

**Never** type a bullet character. Use the numbering config.

```ts
import { LevelFormat, AlignmentType } from "docx";

numbering: {
  config: [
    {
      reference: "bullets",
      levels: [
        { level: 0, format: LevelFormat.BULLET, text: "\u2022",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
        { level: 1, format: LevelFormat.BULLET, text: "\u25E6",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 1440, hanging: 360 } } } },
      ],
    },
    {
      reference: "numbers",
      levels: [
        { level: 0, format: LevelFormat.DECIMAL, text: "%1.",
          alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 720, hanging: 360 } } } },
      ],
    },
  ],
}

// usage
new Paragraph({ numbering: { reference: "bullets", level: 0 },
                children: [new TextRun("Item")] })
```

Each reference maintains an independent counter. Reuse the same reference to
continue numbering; switch references to restart.

---

## Step 6: Headers, footers, page numbers

```ts
import { Header, Footer, PageNumber, AlignmentType } from "docx";

headers: {
  default: new Header({
    children: [new Paragraph({
      alignment: AlignmentType.RIGHT,
      children: [new TextRun({ text: "Q4 FY25 Review",
                               color: "6B7280", size: 18 })],
      border: { bottom: { style: BorderStyle.SINGLE, size: 6,
                          color: "1E2761", space: 4 } },
    })],
  }),
},
footers: {
  default: new Footer({
    children: [new Paragraph({
      alignment: AlignmentType.CENTER,
      children: [
        new TextRun({ text: "Page ", color: "6B7280", size: 18 }),
        new TextRun({ children: [PageNumber.CURRENT], color: "6B7280", size: 18 }),
        new TextRun({ text: " of ",                 color: "6B7280", size: 18 }),
        new TextRun({ children: [PageNumber.TOTAL_PAGES],
                      color: "6B7280", size: 18 }),
      ],
    })],
  }),
},
```

For a two-column footer (left text, right page number), use tab stops. Do
**not** use a table — empty cells render as a visible box in the footer.

---

## Step 7: Using the `pipeshub-docs` helper (recommended)

The helper wraps `docx-js` with high-level primitives that apply theme,
margins, page size, styles, header/footer and numbering config for you.

```ts
import { Report, THEMES } from "pipeshub-docs";
import * as path from "path";

const report = new Report({
  theme: THEMES.corporate,
  title: "Q4 FY25 Business Review",
  author: "pipeshub",
  paperSize: "letter",
});

report.coverPage({
  title: "Q4 FY25",
  subtitle: "Business review",
  author: "Prepared by Finance",
  date: "February 2026",
});
report.tableOfContents();
report.heading1("Executive Summary");
report.paragraph("Revenue grew 18% year over year...");
report.calloutBox("Headline: NRR held at 124% with flat logo churn.");
report.heading2("Key metrics");
report.table({
  headers: ["Metric", "Q3", "Q4"],
  rows: [
    ["Revenue ($M)", "58",  "67"],
    ["NRR",          "122%", "124%"],
    ["New logos",    "24",  "32"],
  ],
});
report.pageBreak();
report.heading1("Risks");
report.bulletList([
  "Pipeline coverage below 3x for enterprise",
  "Two at-risk accounts >$1M ARR",
]);
await report.save(path.join(process.env.OUTPUT_DIR!, "q4-review.docx"));
```

---

## Step 8: Using `docx-js` directly (when the helper is too restrictive)

Everything the helper does can be built manually. See `pptx.md` for the
general principle — prefer the helper so you don't accidentally produce A4
output, percentage widths, or unicode bullets.

### `docx-js` pitfalls (these corrupt the file or look broken)

1. **Set page size explicitly.** Default is A4.
2. **Pass the *short* edge as `width` even for landscape.** `docx-js` swaps
   width/height internally when you set `orientation: PageOrientation.LANDSCAPE`.
3. **Never use `\n` in a `TextRun`.** Create separate `Paragraph`s.
4. **Never manual bullets.** Use `LevelFormat.BULLET` via `numbering.config`.
5. **`PageBreak` must live inside a `Paragraph`.** Standalone is invalid XML.
6. **`ImageRun` requires `type: "png" | "jpg" | "gif" | "bmp" | "svg"`.**
7. **Tables need dual widths** and `WidthType.DXA`. Not percentages.
8. **`ShadingType.CLEAR`**, never `SOLID`.
9. **TOC only picks up `HeadingLevel.*` paragraphs.** Custom styles don't
   show up.
10. **`outlineLevel` is required on heading styles** (0 for H1).

---

## Step 9: Editing an existing `.docx` template

Use `python-docx` and preserve the user's styles.

```python
import os
from docx import Document

doc = Document(os.environ["TEMPLATE_PATH"])

for style in doc.styles:
    print(style.name, style.type)

p = doc.add_paragraph(style="Heading 1")
p.add_run("Q4 Business Review")
doc.add_paragraph(
    "Revenue grew 18% year over year driven by enterprise expansion.",
    style="Body Text",
)
doc.save(os.path.join(os.environ["OUTPUT_DIR"], "edited.docx"))
```

Call `coding_sandbox.ingest_template` first — it returns a structured
summary of available styles, section properties, and theme colors so you
know what's already in the template before you add content.

For find-and-replace across an existing template, unpack the `.docx` (it is
a zip), edit `word/document.xml` with string replacement, and zip it back
up. Use smart quotes (`&#x201C;` `&#x201D;` `&#x2019;`) in new content so
typography matches the original.

---

## Step 10: Visual QA

Always render the document after you write it. Call
`coding_sandbox.render_artifact_preview` on the `.docx` — it uses LibreOffice
to produce one JPEG per page.

Inspect for:

- Text running off the right margin or colliding with the header / footer
- Table columns overflowing the page width (check the sum of `columnWidths`)
- Orphaned headings at the bottom of a page (should flow to next page)
- Widow / orphan lines (single line of a paragraph stranded on a new page)
- Inconsistent spacing above headings
- Low-contrast header / footer text
- Page numbering off by one (cover pages often shouldn't be numbered)
- Leftover template placeholders (`[Company]`, `xxxx`, `TBD`)

Iterate: fix → regenerate → re-preview. Cap at 3 iterations per tool call.
