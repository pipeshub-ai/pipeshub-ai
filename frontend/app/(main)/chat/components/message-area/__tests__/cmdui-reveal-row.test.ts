/**
 * CMDUI: a built-in's result row lands after the last reply, under the composer. In a manual test every command typed in a
 * chat with two replies showed nothing on screen (`/status` at y=1397 with the composer's top at 781). The store
 * names the row the person typed last; the message list scrolls it into the part of the scroller the composer
 * does not cover.
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { useBuiltinResults } from '../../../builtin-results';
import { revealScrollTop } from '../reveal-row';

const VIEW = { viewTop: 0, viewBottom: 780, scrollTop: 600, padding: 16 };

describe('revealScrollTop', () => {
  it('leaves a row that is already in view where it is', () => {
    expect(revealScrollTop({ ...VIEW, rowTop: 100, rowBottom: 400 })).toBeNull();
  });

  it('lifts a row under the composer until its end sits above it', () => {
    expect(revealScrollTop({ ...VIEW, rowTop: 1397, rowBottom: 1593 })).toBe(600 + (1593 - 780) + 16);
  });

  it('shows the start of a row taller than the space above the composer', () => {
    expect(revealScrollTop({ ...VIEW, rowTop: 611, rowBottom: 1385 })).toBe(600 + 611 - 16);
  });

  it('brings back a row scrolled above the top', () => {
    expect(revealScrollTop({ ...VIEW, rowTop: -200, rowBottom: -50 })).toBe(600 - 200 - 16);
  });

  it('never asks for a negative scroll position', () => {
    expect(revealScrollTop({ ...VIEW, scrollTop: 0, rowTop: -10, rowBottom: 20 })).toBe(0);
  });
});

describe('the row to reveal', () => {
  beforeEach(() => useBuiltinResults.setState({ rows: {}, reveal: null }));

  it('is the row just typed, and again when its result settles', () => {
    const store = useBuiltinResults.getState();
    const id = store.add('slot-1', { typed: '/status', afterPairs: 2, result: { kind: 'loading' } });
    const added = useBuiltinResults.getState().reveal;
    expect(added).toMatchObject({ key: 'slot-1', id });
    store.update('slot-1', id, { kind: 'note', text: 'done' });
    const settled = useBuiltinResults.getState().reveal;
    expect(settled).toMatchObject({ key: 'slot-1', id });
    expect(settled!.seq).toBeGreaterThan(added!.seq);
  });

  it('follows a live row that settles under its saved id, and ignores rows read back on open', () => {
    const store = useBuiltinResults.getState();
    const id = store.add('slot-1', { typed: '/upload', afterPairs: 1, result: { kind: 'loading' } });
    store.replace('slot-1', id, { id: 'upload-revision-3', typed: '/upload', afterPairs: 1, result: { kind: 'uploaded', revision: 3 } });
    expect(useBuiltinResults.getState().rows['slot-1']!.map((r) => r.id)).toEqual(['upload-revision-3']);
    const moved = useBuiltinResults.getState().reveal;
    expect(moved).toMatchObject({ key: 'slot-1', id: 'upload-revision-3' });
    store.put('slot-1', { id: 'undo-revision-4', typed: 'Restored', afterPairs: 0, result: { kind: 'undone', revision: 4 } });
    expect(useBuiltinResults.getState().reveal).toEqual(moved);
    store.update('slot-1', 'undo-revision-4', { kind: 'undone', revision: 4, files: 1 });
    expect(useBuiltinResults.getState().reveal).toEqual(moved);
  });
});
