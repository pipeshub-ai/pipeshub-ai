/**
 * R40-TEXT (ADR-TEXT-01), the web client's half: the display-safe rule (SEC-B-04) and the spreadsheet-cell rule
 * (SEC-B-06) replay the shared vectors here as in Python and Node, the preview's destination keys are the worker's
 * list, and the chat's table export follows the shared cell rule.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { displaySafe, punycodeHosts, safeHost, type DisplayMode } from '../display-safe';
import { spreadsheetCell } from '../spreadsheet-cell';
import { DESTINATION_KEYS } from '@/app/(main)/chat/approval-preview';
import { rowsToCsv } from '@/app/(main)/chat/utils/table-csv';

const VECTORS = path.resolve(__dirname, '../../../../contracts/harness/vectors');
const read = <T,>(name: string): T => JSON.parse(readFileSync(path.join(VECTORS, name), 'utf8')) as T;

const display = read<{
  destinationArgs: string[];
  text: Array<{ name: string; mode: DisplayMode; in: string; out: string }>;
  hosts: Array<{ name: string; in: string; out: string }>;
}>('display-safe.json');
const cells = read<{ cases: Array<{ name: string; in: string; out: string }> }>('spreadsheet-cells.json');

describe('R40-TEXT: the display-safe vector (web client)', () => {
  it.each(display.text.map((c) => [c.name, c] as const))('text: %s', (_name, c) => {
    const shown = displaySafe(c.in, c.mode);
    expect(shown).toBe(c.out);
    expect(displaySafe(shown, c.mode)).toBe(shown);
  });

  it.each(display.hosts.map((c) => [c.name, c] as const))('host: %s', (_name, c) => {
    expect(safeHost(c.in)).toBe(c.out);
  });

  it("names the worker's destination arguments", () => {
    expect([...DESTINATION_KEYS]).toEqual(display.destinationArgs);
  });

  it('decodes a punycode host for "looks like"', () => {
    expect(punycodeHosts('https://xn--ppeshub-rog.com/x')).toEqual([{ ascii: 'xn--ppeshub-rog.com', unicode: 'pіpeshub.com' }]);
  });
});

describe('R40-TEXT: the spreadsheet-cell vector (web client)', () => {
  it.each(cells.cases.map((c) => [c.name, c] as const))('cell: %s', (_name, c) => {
    expect(spreadsheetCell(c.in)).toBe(c.out);
  });

  it('the table export writes every cell by the shared rule', () => {
    const row = cells.cases.map((c) => c.in);
    const csv = rowsToCsv([row]);
    const quoted = (cell: string): string => (/[",\r\n]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell);
    expect(csv).toBe(cells.cases.map((c) => quoted(c.out)).join(','));
  });
});
