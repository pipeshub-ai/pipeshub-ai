import { beforeEach, describe, expect, it, vi } from 'vitest';

const saveTextFile = vi.hoisted(() => vi.fn());
vi.mock('../save-text-file', () => ({ saveTextFile }));

import { downloadTableCsv, rowsToCsv, tableCsvFileName, tableNodeToRows, type TableNode } from '../table-csv';

beforeEach(() => saveTextFile.mockReset());

const text = (value: string): TableNode => ({ type: 'text', value });
const el = (tagName: string, ...children: TableNode[]): TableNode => ({ type: 'element', tagName, children });
const row = (tag: 'th' | 'td', ...cells: Array<string | TableNode[]>): TableNode =>
  el('tr', ...cells.map((c) => el(tag, ...(typeof c === 'string' ? [text(c)] : c))));

function table(header: string[], ...body: string[][]): TableNode {
  return el('table', el('thead', row('th', ...header)), el('tbody', ...body.map((r) => row('td', ...r))));
}

describe('tableNodeToRows', () => {
  it('reads the header row first, then each body row', () => {
    expect(tableNodeToRows(table(['Name', 'Qty'], ['Apples', '3'], ['Pears', '5']))).toEqual([
      ['Name', 'Qty'],
      ['Apples', '3'],
      ['Pears', '5'],
    ]);
  });

  it('reads the text inside bold, links and other inline elements', () => {
    const node = el('table', el('tbody', row('td', [el('strong', text('Total')), text(' cost')], [el('a', text('site'))])));

    expect(tableNodeToRows(node)).toEqual([['Total cost', 'site']]);
  });

  it('drops source markers, which are badges in the chat and not data', () => {
    const node = table(
      ['Claim'],
      ['Revenue grew 12% [1]'],
      ['Costs fell [2] [3]'],
      ['Both agree [1][2]'],
      ['Ends a sentence.[4]'],
      ['[5] Starts with one'],
    );

    expect(tableNodeToRows(node)).toEqual([
      ['Claim'],
      ['Revenue grew 12%'],
      ['Costs fell'],
      ['Both agree'],
      ['Ends a sentence.'],
      ['Starts with one'],
    ]);
  });

  it('drops a source written as a link, which the parser leaves as a link with only a number', () => {
    const node = el(
      'table',
      el(
        'tbody',
        row('td', [text('Costs fell '), el('a', text('2')), text(' sharply')]),
        row('td', [text('Both agree'), el('a', text('1')), el('a', text('2'))]),
        row('td', [text('See '), el('a', text('the docs'))]),
        row('td', [text('Year '), el('a', text('2024'))]),
      ),
    );

    expect(tableNodeToRows(node)).toEqual([
      ['Costs fell sharply'],
      ['Both agree'],
      ['See the docs'],
      ['Year 2024'],
    ]);
  });

  it('keeps an index that is part of the cell, such as a[0] or m[1][2]', () => {
    const node = table(['Code'], ['Read a[0] first'], ['Then m[1][2] and f(x)[3]']);

    expect(tableNodeToRows(node)).toEqual([['Code'], ['Read a[0] first'], ['Then m[1][2] and f(x)[3]']]);
  });

  it('turns line breaks and runs of spaces into single spaces', () => {
    const node = el('table', el('tbody', row('td', [text('line one'), el('br'), text('  line   two\n')])));

    expect(tableNodeToRows(node)).toEqual([['line one line two']]);
  });

  it('keeps an empty cell as an empty string so the columns stay aligned', () => {
    expect(tableNodeToRows(table(['A', 'B', 'C'], ['1', '', '3']))).toEqual([
      ['A', 'B', 'C'],
      ['1', '', '3'],
    ]);
  });

  it('skips a row that has no cells', () => {
    expect(tableNodeToRows(el('table', el('tbody', el('tr'), row('td', 'x'))))).toEqual([['x']]);
  });

  it('returns no rows when there is no node', () => {
    expect(tableNodeToRows(undefined)).toEqual([]);
  });
});

describe('rowsToCsv', () => {
  it('joins cells with commas and rows with CRLF', () => {
    expect(rowsToCsv([['a', 'b'], ['1', '2']])).toBe('a,b\r\n1,2');
  });

  it('quotes a cell with a comma, a quote or a line break, and doubles the quotes in it', () => {
    expect(rowsToCsv([['a,b', 'say "hi"', 'two\nlines', 'plain']])).toBe('"a,b","say ""hi""","two\nlines",plain');
  });

  it('writes an empty cell as nothing', () => {
    expect(rowsToCsv([['a', '', 'c']])).toBe('a,,c');
  });

  it.each([
    ['=SUM(A1:A9)', "'=SUM(A1:A9)"],
    ['@cmd', "'@cmd"],
    ['+1+2', "'+1+2"],
    ['-2+3', "'-2+3"],
    ['\tpadded', "'\tpadded"],
    ['=HYPERLINK("http://x")', `"'=HYPERLINK(""http://x"")"`],
  ])('saves %j as text, since a spreadsheet would run it as a formula', (cell, saved) => {
    expect(rowsToCsv([[cell]])).toBe(saved);
  });

  it.each(['-5', '+5', '-1,234.50', '12%', '-', '1+2', 'a=b', 'email@example.com'])(
    'leaves %j as it is',
    (cell) => {
      expect(rowsToCsv([[cell]])).toBe(cell.includes(',') ? `"${cell}"` : cell);
    },
  );
});

describe('downloadTableCsv', () => {
  it('saves a CSV with a byte order mark so Excel reads non-Latin text', () => {
    downloadTableCsv([['Nom', 'Prix'], ['Café', '3,5']]);

    expect(saveTextFile).toHaveBeenCalledTimes(1);
    const [name, content, mime] = saveTextFile.mock.calls[0];
    expect(name).toMatch(/^nom-\d{8}-\d{6}\.csv$/);
    expect(mime).toBe('text/csv');
    expect(content).toBe('\uFEFFNom,Prix\r\nCafé,"3,5"\r\n');
  });
});

describe('tableCsvFileName', () => {
  const at = new Date(2026, 9, 27, 9, 5, 7);

  it('names the file after the first header and the local time', () => {
    expect(tableCsvFileName([['Industry Name', 'Count']], at)).toBe('industry-name-20261027-090507.csv');
  });

  it('drops accents and symbols from the header', () => {
    expect(tableCsvFileName([['Café / Prix (€)']], at)).toBe('cafe-prix-20261027-090507.csv');
  });

  it('keeps non-Latin headers', () => {
    expect(tableCsvFileName([['姓名']], at)).toBe('姓名-20261027-090507.csv');
    expect(tableCsvFileName([['売上 (円)']], at)).toBe('売上-円-20261027-090507.csv');
    expect(tableCsvFileName([['Выручка']], at)).toBe('выручка-20261027-090507.csv');
    expect(tableCsvFileName([['हिन्दी']], at)).toBe('हिन्दी-20261027-090507.csv');
  });

  it('drops path separators, control characters and characters Windows rejects', () => {
    expect(tableCsvFileName([['a/b\\c:d*e?f"g<h>i|j\u0007k']], at)).toBe('a-b-c-d-e-f-g-h-i-j-k-20261027-090507.csv');
    expect(tableCsvFileName([['../../売上']], at)).toBe('売上-20261027-090507.csv');
  });

  it('cuts a long header by character, not code unit', () => {
    const name = tableCsvFileName([['𝒜'.repeat(50)]], at);
    expect(name).toBe(`${'a'.repeat(40)}-20261027-090507.csv`);
  });

  it('falls back to table- when the header has nothing usable', () => {
    expect(tableCsvFileName([['#'], ['1']], at)).toBe('table-20261027-090507.csv');
    expect(tableCsvFileName([], at)).toBe('table-20261027-090507.csv');
  });
});
