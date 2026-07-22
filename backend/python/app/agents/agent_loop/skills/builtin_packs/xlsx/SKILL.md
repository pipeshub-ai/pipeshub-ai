---
name: xlsx
description: "Use this skill whenever the user asks for an Excel spreadsheet, a .xlsx file, or a workbook with formulas/pivots/multiple sheets — creating one from scratch or editing an existing one. Covers the rule that computed values must be written as real Excel formulas (never hardcoded numbers), and a static formula-safety check. Formulas are NOT recalculated in this environment — see 'Not supported'."
metadata:
  agent-loop:
    version: 1.0.0
    category: documents
    subcategory: excel
    tags: [xlsx, excel, spreadsheets, formulas, exceljs]
    status: active
    source: builtin
    pack_name: xlsx
    pack_version: 1.2.1
---

# Excel spreadsheets (.xlsx)

## Quick Reference

| Task | Default (Node) | Fallback (Python) |
|------|-----------------|--------------------|
| Create / edit a workbook | `exceljs` — write a TypeScript program (packages: ["exceljs"]) and run it via your coding tool; pre-installed in the standard sandbox image | `openpyxl` — reach for this only when the workbook is a byproduct of a larger pandas-based analysis already running in Python (see `data-analysis`), not as a first choice |
| Bulk tabular data in/out | `exceljs` (`worksheet.addRows(records)`) | `pandas` (`pd.read_excel`/`DataFrame.to_excel`) when the data is already a DataFrame |
| Static formula sanity-check | `node skills/xlsx/scripts/verify_formulas.ts <file.xlsx>` (via `tsx`, already installed by the sandbox's TypeScript runtime) | `python skills/xlsx/scripts/verify_formulas.py <file.xlsx>` |

**Default to `exceljs` in TypeScript** for any new spreadsheet-creation task, per this environment's Node-first policy for office-document generation — reach for `openpyxl`/`pandas` only when the workbook is genuinely a byproduct of Python-side data work you're already doing (e.g. exporting a `data-analysis` result), not as a starting point. Both are already installed/allowlisted — no extra setup either way.

## The one rule that matters most: write real formulas, not computed values

When a cell's value is the result of a calculation the user will want to keep updating (a total, a percentage, a lookup), write it as an actual Excel formula, never as the number your code computed on the JS/Python side. A hardcoded number looks correct today but silently goes stale the moment the user edits an input cell — which defeats the entire purpose of giving them a spreadsheet instead of a static table. This applies to totals, subtotals, percentages-of-total, running balances, and any lookup/reference between sheets.

In `exceljs`, a formula is a cell value shaped as an object, not a string prefixed with `=`:

```typescript
worksheet.getCell("B10").value = { formula: "SUM(B2:B9)" };
```

Exception: raw input data (the numbers the user gave you, or that you extracted from a source document) should be hardcoded values — only *derived* values need to be formulas.

## Creating / editing with exceljs

```typescript
import ExcelJS from "exceljs";

const workbook = new ExcelJS.Workbook();
const sheet = workbook.addWorksheet("Report", {
  views: [{ state: "frozen", ySplit: 1 }], // freeze the header row
});

sheet.columns = [
  { header: "Item", key: "item", width: 32 },
  { header: "Amount", key: "amount", width: 14, style: { numFmt: "#,##0.00" } },
];

sheet.addRows([{ item: "Widgets", amount: 1200 }, { item: "Gadgets", amount: 830 }]);
sheet.getCell(`B${sheet.rowCount + 1}`).value = { formula: `SUM(B2:B${sheet.rowCount})` };

await workbook.xlsx.writeFile("report.xlsx");
```

- Set explicit column widths (`width` in the `columns` definition, or `worksheet.getColumn(n).width = ...`) and number formats (`cell.numFmt = "#,##0.00"` / `"0.0%"`) — an unformatted sheet of raw numbers reads as unfinished.
- Freeze header rows for any sheet with more than ~15 data rows: `worksheet.views = [{ state: "frozen", ySplit: 1 }]` (add `xSplit` too to also freeze leading columns).
- When writing a formula whose range depends on the data you just wrote (e.g. a `SUM` over however many rows got added), compute the range bounds from `worksheet.rowCount`/`actualRowCount`, never a hardcoded row count — otherwise the formula silently omits rows if the dataset grows.
- Sheet/tab names: Excel forbids `: \ / ? * [ ]` and a 31-character limit — sanitize any user-provided sheet name before calling `workbook.addWorksheet(name)`.
- `exceljs` has no auto-fit for column widths — if content length varies a lot, compute a width from the max string length in that column yourself before setting it, rather than leaving a default that truncates.

## Reading a workbook

```typescript
const workbook = new ExcelJS.Workbook();
await workbook.xlsx.readFile("input.xlsx");
const sheet = workbook.getWorksheet("Sheet1"); // or workbook.worksheets[0]
sheet.eachRow({ includeEmpty: false }, (row) => { /* row.values, row.getCell(n) */ });
```

For heavy tabular reshaping (multi-file joins, group-by/aggregate before writing the result out), it's reasonable to read via `pandas.read_excel` into a DataFrame instead — pandas' reshaping API is more ergonomic than hand-rolling the equivalent in `exceljs`, and the DataFrame can still be written out via `pandas.DataFrame.to_excel` (or handed to `exceljs` if you need formulas/styling on the way out that pandas doesn't support).

## Verifying formulas without a recalculation engine

This environment cannot recalculate formulas (no LibreOffice/Excel to open the file and re-run its calculation engine), so a formula's *result* is never re-verified — only its *shape*. `scripts/verify_formulas.ts` (or the Python equivalent, `scripts/verify_formulas.py` — both inspect the same on-disk `.xlsx` format and produce equivalent results, so use whichever matches the sandbox session you already have open) does a best-effort static scan for the most common formula footguns:

- Division formulas whose denominator cell reference is currently blank or `0` in the data you wrote — a likely future `#DIV/0!` the moment that cell is read.
- A range reference (e.g. `SUM(B2:B100)`) that extends beyond the sheet's actual used range — usually a sign the range was hardcoded rather than computed from the data.
- Any cell whose cached value (if the file was previously touched by a real spreadsheet app) is itself an Excel error token (`#REF!`, `#DIV/0!`, `#VALUE!`, `#N/A`, `#NAME?`, `#NULL!`, `#NUM!`).

Run it after writing the file, before handing it to the user: `npx tsx skills/xlsx/scripts/verify_formulas.ts output.xlsx`. It exits non-zero and prints every flagged cell if it finds anything.

**Independently cross-check any number you report in your response** (e.g. "the total is $45,230") by actually computing it in code — since the formula itself is never executed in this environment, the only way to be confident the number you're telling the user about is correct is to have actually computed it yourself, not just written a formula that should compute it.

## Not supported in this environment

- Formula recalculation via a LibreOffice macro (Anthropic's reference implementation's approach) — not available; formulas are written correctly but left uncalculated, and Excel/Google Sheets will calculate them on first open.
