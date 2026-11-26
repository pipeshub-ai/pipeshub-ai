/**
 * EV-15 VR-1..VR-3: the arithmetic behind the chat's virtualized lists, whose rows vary in height.
 */
import { describe, expect, it } from 'vitest';
import { measuredWindow, rowOffsets } from '../measured-window';

describe('EV-15 measured window', () => {
  it('VR-1: offsets add each row’s own size, measured or estimated', () => {
    const sizes = [10, 30, 20];
    expect(Array.from(rowOffsets(3, (i) => sizes[i]))).toEqual([0, 10, 40, 60]);
    expect(Array.from(rowOffsets(0, () => 1))).toEqual([0]);
  });

  it('VR-2: the window holds the rows the view and its overscan intersect, padding the rest', () => {
    // Rows alternate 10 and 90 px: 10,000 rows, 500,000 px.
    const offsets = rowOffsets(10_000, (i) => (i % 2 === 0 ? 10 : 90));
    const window = measuredWindow(offsets, 250_000, 250_600, 200);
    // 249,800 falls in row 4,996 (it starts at 249,800); 250,800 is where row 5,016 starts.
    expect(window.start).toBe(4_996);
    expect(window.end).toBe(5_016);
    expect(window.padTop).toBe(offsets[4_996]);
    expect(window.padTop + (offsets[window.end] - offsets[window.start]) + window.padBottom).toBe(500_000);
  });

  it('VR-3: a view above, below or not laid out still renders a row; an empty list renders none', () => {
    const offsets = rowOffsets(100, () => 20);
    expect(measuredWindow(offsets, -5_000, -4_000, 0)).toMatchObject({ start: 0, end: 1, padTop: 0 });
    expect(measuredWindow(offsets, 9_000, 9_500, 0)).toMatchObject({ start: 99, end: 100, padBottom: 0 });
    expect(measuredWindow(offsets, 0, 0, 0)).toMatchObject({ start: 0, end: 1 });
    expect(measuredWindow(rowOffsets(0, () => 1), 0, 100, 0)).toEqual({ start: 0, end: 0, padTop: 0, padBottom: 0 });
  });
});
