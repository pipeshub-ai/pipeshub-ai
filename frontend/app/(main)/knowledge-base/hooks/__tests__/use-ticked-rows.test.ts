import { describe, expect, it } from 'vitest';
import { renderHook } from '@testing-library/react';
import { toPendingChatNode, useTickedRows } from '../use-ticked-rows';

const SPEC = { id: 'rec-spec', name: 'spec.pdf', nodeType: 'record', connector: 'KB' };
const NOTES = { id: 'rec-notes', name: 'notes.txt', nodeType: 'record', connector: 'KB' };
const DESIGNS = { id: 'folder-designs', name: 'Designs', nodeType: 'folder' };

type Props = { rows: (typeof SPEC | typeof DESIGNS)[]; ticked: Set<string> };
const render = (initialProps: Props) =>
  renderHook(({ rows, ticked }: Props) => useTickedRows(rows, ticked), { initialProps });
const asChatNodes = (rows: Props['rows']) => rows.map(toPendingChatNode);

describe('useTickedRows', () => {
  it('returns the ticked rows of the page on screen, with what the chat needs to name them', () => {
    const { result } = render({ rows: [DESIGNS, SPEC, NOTES], ticked: new Set(['folder-designs', 'rec-notes']) });

    expect(asChatNodes(result.current)).toEqual([
      { id: 'folder-designs', name: 'Designs', nodeType: 'folder', connector: '' },
      { id: 'rec-notes', name: 'notes.txt', nodeType: 'record', connector: 'KB' },
    ]);
  });

  it('keeps a row ticked on a page that is no longer on screen', () => {
    const { result, rerender } = render({ rows: [SPEC], ticked: new Set(['rec-spec']) });

    rerender({ rows: [NOTES], ticked: new Set(['rec-spec', 'rec-notes']) });

    expect(result.current.map((row) => row.name)).toEqual(['spec.pdf', 'notes.txt']);
  });

  it('keeps the whole row, so a bulk action sees every field of a row ticked on another page', () => {
    const permitted = { ...SPEC, permission: { canDelete: true } };
    const { result, rerender } = render({ rows: [permitted], ticked: new Set(['rec-spec']) });
    rerender({ rows: [NOTES], ticked: new Set(['rec-spec', 'rec-notes']) });
    expect(result.current[0]).toBe(permitted);
  });

  it('forgets a row once it is unticked or the selection is cleared', () => {
    const { result, rerender } = render({ rows: [SPEC, NOTES], ticked: new Set(['rec-spec', 'rec-notes']) });

    rerender({ rows: [SPEC, NOTES], ticked: new Set(['rec-notes']) });
    expect(result.current.map((row) => row.name)).toEqual(['notes.txt']);

    rerender({ rows: [SPEC, NOTES], ticked: new Set() });
    expect(result.current).toEqual([]);
  });

  it('treats a row of the older listing shape by its kind', () => {
    const { result } = render({
      rows: [{ id: 'f1', name: 'Old folder', type: 'folder' }, { id: 'r1', name: 'old.txt', type: 'file' }] as never,
      ticked: new Set(['f1', 'r1']),
    });

    expect(asChatNodes(result.current).map((row) => row.nodeType)).toEqual(['folder', 'record']);
  });
});
