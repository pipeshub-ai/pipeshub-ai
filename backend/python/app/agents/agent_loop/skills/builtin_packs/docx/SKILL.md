---
name: docx
description: "Use this skill whenever the user asks for a Word document, a .docx file, or a report/memo/letter/proposal delivered as one — creating a new document from scratch, editing an existing .docx, or reading/summarizing the contents of one. Covers page layout, headings, tables, numbered/bulleted lists, page breaks, and a table of contents. Does not cover converting a .docx to PDF or .doc to .docx (see file-conversion for what conversions ARE supported in this environment)."
metadata:
  agent-loop:
    version: 1.0.0
    category: documents
    subcategory: word
    tags: [docx, word, documents, reports]
    status: active
    source: builtin
    requires: [office-utils]
    pack_name: docx
    pack_version: 1.1.1
---

# Word documents (.docx)

## Quick Reference

| Task | Approach |
|------|----------|
| Create a new .docx from scratch | `docx` (docx-js) — write a TypeScript program (packages: ["docx"]) and run it via your coding tool |
| Edit an existing .docx | office-utils: unpack → edit XML → pack (see that skill) |
| Read / extract text from a .docx | `python-docx` or `docx2python` (Python, already installed — no `pandoc` needed) |

**Always CREATE documents with `docx` (docx-js) in TypeScript — never with `python-docx`.** `python-docx` is in this environment for *reading* existing documents only. Run the TypeScript program via whatever coding surface you have: the `run_code` tool directly (language="typescript", packages=["docx"]), or the `coding_agent` delegate — in that case restate these library/language requirements in its goal. The library is pre-installed in the standard sandbox image, so the install step is an instant no-op there.

## Creating a new document (docx-js)

`docx-js` (npm package `docx`) builds a document as a tree of JS objects, then serializes it. The hard-won pitfalls below are the difference between a `.docx` that opens cleanly in Word/Google Docs and one that shows a repair prompt or renders with the wrong layout — verify your generated file against every one of these before considering the task done.

1. **Page size and margins are in DXA units (twentieths of a point), not inches or pixels.** A US Letter page (8.5in x 11in) is `{ width: 12240, height: 15840 }` DXA. Always set `Document.sections[0].properties.page.size` explicitly — don't rely on the library default, which may not match what the user expects.
2. **Table column widths must use `WidthType.DXA`, not `WidthType.PERCENTAGE`, for predictable rendering.** Percentage widths compute inconsistently across renderers (Word vs. Google Docs vs. LibreOffice) when the table has merged cells or nested tables. Compute explicit DXA widths that sum to the usable page width (page width minus left+right margins).
3. **Numbered/bulleted lists need a `LevelFormat` numbering config registered on the document, not literal unicode bullet characters (`•`, `-`) typed into a text run.** A literal bullet character is not a list to Word — it can't be renumbered, indented, or styled as one. Define a `numbering.config` with `LevelFormat.BULLET` (or `LevelFormat.DECIMAL` for numbered lists) and reference it from each paragraph's `numbering` property.
4. **A page break is a property of a `Paragraph`, not a standalone element.** Use `new Paragraph({ children: [new PageBreak()] })` — inserting a bare `PageBreak()` outside a paragraph will fail to serialize correctly in some docx-js versions.
5. **A table of contents (`TableOfContents`) only picks up paragraphs styled with a real Heading style (`HeadingLevel.HEADING_1` etc.), never plain bold text.** If the user wants a working, clickable/updatable ToC, every section title must use a heading style — and the ToC itself needs a "right-click → Update Field" note in your response, since docx-js writes the ToC's field code but cannot pre-compute the resulting page numbers without a real layout engine.
6. **Always set explicit `size` (half-points) on any `TextRun` where the default (usually 11pt/22 half-points) doesn't match the user's request** — half-points, not points: 12pt text is `size: 24`.

## Editing an existing .docx

Use the `office-utils` skill's unpack → edit → pack cycle: the document body lives in `word/document.xml` inside the unpacked tree. Locate the `<w:p>` (paragraph) or `<w:tbl>` (table) you need to change, make the smallest edit that achieves the goal, and repack. Do not attempt to regenerate the whole document via docx-js just to change one paragraph — you will lose any formatting/styles the original author applied that aren't represented in your regenerated version.

## Reading a .docx

- `python-docx`: `Document(path).paragraphs` / `.tables` for structured access (paragraph text + style, table cell grids).
- `docx2python`: better for a fast, structure-preserving plain-text dump when you just need the content, not to manipulate specific elements.

Both are already installed in the sandbox — no `install_packages` call needed.

## Not supported in this environment

- `.doc` → `.docx` conversion, and `.docx` → PDF rendering — both require LibreOffice, which is not installed in the sandbox yet. Tell the user this rather than attempting a workaround that will silently produce a wrong result.
