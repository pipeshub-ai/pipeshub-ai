import { saveTextFile } from './save-text-file';

/** The parts of a markdown parser node that reading a table needs. */
export interface TableNode {
  type: string;
  tagName?: string;
  value?: string;
  children?: TableNode[];
}

// A run of `[3]` markers: sources the chat turns into badges, not table data.
const CITATION_RUN = /(\s*)(?:\[\d+\])+/g;

// An index tight against a word or bracket, as in `a[0]` or `m[1][2]`, is content, not a source.
function stripCitationMarkers(text: string): string {
  return text.replace(CITATION_RUN, (match, space: string, offset: number) => {
    const attached = space === '' && offset > 0 && /[\w\])]/.test(text[offset - 1]);
    return attached ? match : '';
  });
}

// The backend writes a web source as `[2](https://…)`, which the parser turns into a link whose
// text is only the number. Its text alone would leave the number behind in the cell.
const CITATION_LINK_TEXT = /^\d{1,3}$/;

function textOf(node: TableNode): string {
  if (node.type === 'text') return node.value ?? '';
  if (node.tagName === 'br') return ' ';
  const text = (node.children ?? []).map(textOf).join('');
  return node.tagName === 'a' && CITATION_LINK_TEXT.test(text.trim()) ? '' : text;
}

function cellText(cell: TableNode): string {
  return stripCitationMarkers(textOf(cell)).replace(/\s+/g, ' ').trim();
}

function collectRows(node: TableNode, rows: string[][]): void {
  if (node.tagName === 'tr') {
    const cells = (node.children ?? []).filter((c) => c.tagName === 'th' || c.tagName === 'td');
    if (cells.length > 0) rows.push(cells.map(cellText));
    return;
  }
  for (const child of node.children ?? []) collectRows(child, rows);
}

export function tableNodeToRows(table: TableNode | undefined): string[][] {
  const rows: string[][] = [];
  if (table) collectRows(table, rows);
  return rows;
}

// A spreadsheet runs a cell that starts like this as a formula. The text here comes from a model
// that may be repeating a document it read, so such a cell is saved as text instead.
const FORMULA_START = /^[=@\t\r]/;
const SIGN_START = /^[+-]/;
const PLAIN_NUMBER = /^[+-]?\d[\d.,]*%?$/;

function csvCell(value: string): string {
  const safe =
    FORMULA_START.test(value) || (SIGN_START.test(value) && value.length > 1 && !PLAIN_NUMBER.test(value))
      ? `'${value}`
      : value;
  return /[",\r\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
}

export function rowsToCsv(rows: string[][]): string {
  return rows.map((row) => row.map(csvCell).join(',')).join('\r\n');
}

/** The byte order mark lets Excel read accented and non-Latin text as UTF-8. */
const UTF8_BOM = '\uFEFF';

/**
 * Everything but letters, numbers and combining marks, so non-Latin headers
 * keep their text while path separators, control characters and the
 * characters Windows rejects (<>:"/\|?*) are dropped. Built at runtime because
 * the es5 target rejects the `u` flag on a regex literal.
 */
const NON_FILENAME_CHARS = new RegExp('[^\\p{L}\\p{N}\\p{M}]+', 'gu');

function pad2(n: number): string {
  return String(n).padStart(2, '0');
}

/**
 * `<first header>-YYYYMMDD-HHmmss.csv` (local time), or `table-…` when the
 * header has nothing usable, so repeated downloads do not all land as `table.csv`.
 */
export function tableCsvFileName(rows: string[][], now: Date = new Date()): string {
  const cleaned = (rows[0]?.[0] ?? '')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .normalize('NFC')
    .toLowerCase()
    .replace(NON_FILENAME_CHARS, '-')
    .replace(/^-+|-+$/g, '');
  // By code point, so the cut never splits a surrogate pair.
  const slug = Array.from(cleaned).slice(0, 40).join('').replace(/-+$/, '');
  const stamp =
    `${now.getFullYear()}${pad2(now.getMonth() + 1)}${pad2(now.getDate())}` +
    `-${pad2(now.getHours())}${pad2(now.getMinutes())}${pad2(now.getSeconds())}`;
  return `${slug || 'table'}-${stamp}.csv`;
}

export function downloadTableCsv(rows: string[][]): void {
  saveTextFile(tableCsvFileName(rows), `${UTF8_BOM}${rowsToCsv(rows)}\r\n`, 'text/csv');
}
