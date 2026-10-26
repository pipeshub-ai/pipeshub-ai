import { describe, expect, it } from 'vitest';
import { looksLikeJson, safeLink, toolResultView } from '../tool-result-view';

describe('toolResultView', () => {
  it('keeps a table, filling short rows and capping its size', () => {
    const view = toolResultView({
      kind: 'records',
      columns: ['A', 'B', 'C', 'D', 'E', 'F'],
      rows: [
        { cells: ['1'], url: 'https://example.com/1' },
        { cells: ['x'.repeat(1000), 2, null] },
        'junk',
        ...Array.from({ length: 30 }, () => ({ cells: ['r'] })),
      ],
      total: 50,
    });

    expect(view?.kind).toBe('records');
    if (view?.kind !== 'records') return;
    expect(view.columns).toHaveLength(5);
    expect(view.rows).toHaveLength(19);
    expect(view.rows[0]).toEqual({ cells: ['1', '', '', '', ''], url: 'https://example.com/1' });
    expect(view.rows[1].cells[0]).toHaveLength(300);
    expect(view.rows[1].cells[1]).toBe('');
    expect(view.total).toBe(50);
  });

  it("keeps a record's fields and drops fields without a label", () => {
    expect(
      toolResultView({ kind: 'fields', fields: [{ label: 'Status', value: 'Done' }, { value: 'orphan' }], url: 'javascript:x' })
    ).toEqual({ kind: 'fields', fields: [{ label: 'Status', value: 'Done' }] });
  });

  it.each([null, 'records', [], {}, { kind: 'records', columns: [], rows: [{ cells: ['a'] }] }, { kind: 'records', columns: ['A'], rows: [] }, { kind: 'fields', fields: [] }, { kind: 'text', text: 'x' }])(
    'drops %j',
    (value) => {
      expect(toolResultView(value)).toBeNull();
    }
  );
});

describe('safeLink', () => {
  it.each([
    ['https://example.com/a?b=1', 'https://example.com/a?b=1'],
    ['http://example.com', 'http://example.com/'],
    ['javascript:alert(1)', undefined],
    ['data:text/html,x', undefined],
    ['//example.com/x', undefined],
    ['/relative', undefined],
    ['https://user:pass@example.com/', undefined],
    [42, undefined],
  ])('%j → %j', (value, expected) => {
    expect(safeLink(value)).toBe(expected);
  });
});

describe('looksLikeJson', () => {
  it('tells JSON from prose', () => {
    expect(looksLikeJson('  {"a": 1}')).toBe(true);
    expect(looksLikeJson('[IMPORTANT: a notice]\n{"a": 1}')).toBe(true);
    expect(looksLikeJson('Found the page.')).toBe(false);
    expect(looksLikeJson('')).toBe(false);
  });
});
