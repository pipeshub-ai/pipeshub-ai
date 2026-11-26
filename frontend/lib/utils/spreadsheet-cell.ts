/**
 * A text cell as a file a spreadsheet opens must hold it (SEC-B-06, ADR-TEXT-01). A spreadsheet runs a cell that
 * starts with `=`, `+`, `-` or `@` (or their full-width forms, or any of them after leading blanks) as a formula,
 * and a formula can send neighbouring cells out (`HYPERLINK`, `WEBSERVICE`) or start a program (DDE). Such a cell is
 * written with a leading `'` so it opens as text; a plain number keeps its sign (`-42` is a value).
 *
 * The sets are `contracts/harness/vectors/spreadsheet-cells.json`'s, as Python's
 * `harness_core.security.spreadsheet_cells` carries them. Node (`src/libs/utils/spreadsheet-cell.ts`) and the web
 * client (`frontend/lib/utils/spreadsheet-cell.ts`) hold the same code, each formatted by its own tree, and each test
 * suite replays the vector.
 */

export const SPREADSHEET_PREFIX = "'";
export const TRIGGERS = '=+-@\uff1d\uff0b\uff0d\uff20';
export const STARTS_ALWAYS = '\t\r';
/** A sign alone is a table's placeholder ("-" for none), not a formula: nothing follows it to run. */
export const SIGNS = '+-\uff0b\uff0d';
export const LEADING =
  '\t\n\u000b\u000c\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u200b' +
  '\u2028\u2029\u202f\u205f\u3000\ufeff';

const PLAIN_NUMBER = /^[+-]?(?:[0-9][0-9.,]*|[.,][0-9]+)(?:[eE][+-]?[0-9]+)?%?$/;

/** `value` as a spreadsheet must read it: as text, never as a formula. */
export function spreadsheetCell(value: string): string {
  if (value === '') return value;
  if (STARTS_ALWAYS.indexOf(value.charAt(0)) !== -1) return SPREADSHEET_PREFIX + value;
  let i = 0;
  while (i < value.length && LEADING.indexOf(value.charAt(i)) !== -1) i += 1;
  if (i >= value.length || TRIGGERS.indexOf(value.charAt(i)) === -1) return value;
  if (PLAIN_NUMBER.test(value)) return value;
  const rest = value.slice(0, i) + value.slice(i + 1);
  const alone = SIGNS.indexOf(value.charAt(i)) !== -1 && rest.split('').every((ch) => LEADING.indexOf(ch) !== -1);
  return alone ? value : SPREADSHEET_PREFIX + value;
}
