/**
 * Best-effort static scan for common Excel formula footguns, since this
 * environment has no way to actually recalculate a workbook (no
 * LibreOffice/Excel). Does NOT execute formulas — only inspects formula
 * text and any cached values already stored in the file.
 *
 * Equivalent to verify_formulas.py (same checks, same exit-code contract) —
 * pick whichever matches the sandbox session you already have open; both
 * inspect the same on-disk .xlsx format and will flag the same issues.
 *
 * Usage: npx tsx verify_formulas.ts <file.xlsx>
 * Exit code 0 if clean, 1 if any issue was flagged (details printed to stdout).
 */
import ExcelJS from "exceljs";

const ERROR_TOKENS = new Set([
  "#REF!", "#DIV/0!", "#VALUE!", "#N/A", "#NAME?", "#NULL!", "#NUM!",
]);
// Mirrors verify_formulas.py's _RANGE_RE / _DIVISION_RE.
const RANGE_RE = /([A-Za-z_][A-Za-z0-9_.]*!)?(\$?[A-Z]{1,3}\$?\d+):(\$?[A-Z]{1,3}\$?\d+)/g;
const DIVISION_RE = /\/\s*(\$?[A-Z]{1,3}\$?\d+)\b/g;
const CELL_REF_RE = /^\$?([A-Z]{1,3})\$?(\d+)$/;

function columnLetterToNumber(letters: string): number {
  let n = 0;
  for (const ch of letters.toUpperCase()) {
    n = n * 26 + (ch.charCodeAt(0) - 64);
  }
  return n;
}

function parseCellRef(ref: string): { col: number; row: number } | null {
  const match = CELL_REF_RE.exec(ref);
  if (!match) return null;
  return { col: columnLetterToNumber(match[1]), row: Number(match[2]) };
}

function isErrorValue(value: unknown): string | null {
  if (typeof value === "string" && ERROR_TOKENS.has(value.trim())) return value.trim();
  if (value && typeof value === "object" && "error" in (value as Record<string, unknown>)) {
    const err = (value as Record<string, unknown>).error;
    if (typeof err === "string") return err;
  }
  return null;
}

/** Cached/plain numeric-ish value at `ref`, resolving through a formula cell's cached result. */
function cellCurrentValue(worksheet: ExcelJS.Worksheet, ref: string): unknown {
  const cell = worksheet.getCell(ref.replace(/\$/g, ""));
  const value = cell.value as unknown;
  if (value && typeof value === "object" && "result" in (value as Record<string, unknown>)) {
    return (value as Record<string, unknown>).result;
  }
  return value;
}

function checkSheet(worksheet: ExcelJS.Worksheet): string[] {
  const issues: string[] = [];
  const maxRow = worksheet.rowCount;
  const maxCol = worksheet.columnCount;

  worksheet.eachRow({ includeEmpty: false }, (row) => {
    row.eachCell({ includeEmpty: false }, (cell) => {
      const raw = cell.value as unknown;
      const isFormula = !!raw && typeof raw === "object" && "formula" in (raw as Record<string, unknown>);

      if (!isFormula) {
        const errorToken = isErrorValue(raw);
        if (errorToken) {
          issues.push(`${worksheet.name}!${cell.address}: cached error value ${JSON.stringify(errorToken)}`);
        }
        return;
      }

      const formula = String((raw as Record<string, unknown>).formula);

      for (const match of formula.matchAll(RANGE_RE)) {
        if (match[1]) continue; // cross-sheet reference — out of scope for this scan
        const start = parseCellRef(match[2]);
        const end = parseCellRef(match[3]);
        if (!start || !end) continue;
        const rangeMaxRow = Math.max(start.row, end.row);
        const rangeMaxCol = Math.max(start.col, end.col);
        if (rangeMaxRow > maxRow || rangeMaxCol > maxCol) {
          issues.push(
            `${worksheet.name}!${cell.address}: formula ${JSON.stringify("=" + formula)} references range ` +
            `${match[2]}:${match[3]} which extends beyond the sheet's used range ` +
            `(max_row=${maxRow}, max_col=${maxCol}) — check the range was computed from the actual ` +
            `data, not hardcoded`,
          );
        }
      }

      for (const match of formula.matchAll(DIVISION_RE)) {
        const denomRef = match[1];
        const denomValue = cellCurrentValue(worksheet, denomRef);
        if (denomValue === 0 || denomValue === null || denomValue === undefined || denomValue === "") {
          issues.push(
            `${worksheet.name}!${cell.address}: formula ${JSON.stringify("=" + formula)} divides by ` +
            `${denomRef}, whose current value is ${JSON.stringify(denomValue)} — likely #DIV/0! risk`,
          );
        }
      }
    });
  });

  return issues;
}

async function verify(path: string): Promise<string[]> {
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.readFile(path);
  const issues: string[] = [];
  for (const worksheet of workbook.worksheets) {
    issues.push(...checkSheet(worksheet));
  }
  return issues;
}

async function main() {
  const path = process.argv[2];
  if (!path) {
    console.error("usage: npx tsx verify_formulas.ts <file.xlsx>");
    process.exit(2);
  }
  const issues = await verify(path);
  if (issues.length === 0) {
    console.log("No formula issues flagged.");
    process.exit(0);
  }
  console.log(`${issues.length} potential issue(s) flagged (not executed, static scan only):`);
  for (const issue of issues) console.log(`  - ${issue}`);
  process.exit(1);
}

main();
