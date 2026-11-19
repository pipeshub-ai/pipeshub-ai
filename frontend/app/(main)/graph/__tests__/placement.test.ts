import { describe, expect, it } from 'vitest';
import { cellKey, placeNodes, type Cell } from '../model/placement';

const origin: Cell = { c: 0, r: 0 };

describe('placeNodes', () => {
  it('puts callers on the left and callees on the right of the parent', () => {
    const left = placeNodes(new Set(), origin, -1, ['a', 'b', 'c']);
    const right = placeNodes(new Set(), origin, 1, ['a', 'b', 'c']);
    expect(Object.values(left).every((cell) => cell.c < 0)).toBe(true);
    expect(Object.values(right).every((cell) => cell.c > 0)).toBe(true);
  });

  it('never gives two nodes one cell, nor a cell that was taken', () => {
    const occupied = new Set([cellKey({ c: 1, r: 0 }), cellKey({ c: 1, r: 1 })]);
    const ids = Array.from({ length: 80 }, (_, i) => `n${i}`);
    const placed = placeNodes(occupied, origin, 1, ids);
    const cells = Object.values(placed).map(cellKey);
    expect(new Set(cells).size).toBe(ids.length);
    expect(cells).not.toContain('1,0');
    expect(cells).not.toContain('1,1');
  });

  it('gives the same picture whatever order the server returned the ids in', () => {
    const ids = ['m', 'z', 'a', 'k', 'b'];
    const one = placeNodes(new Set(), origin, 1, ids);
    const two = placeNodes(new Set(), origin, 1, [...ids].reverse());
    expect(two).toEqual(one);
  });

  it('records what it took, so a second expand does not overlap the first', () => {
    const occupied = new Set<string>();
    const first = placeNodes(occupied, origin, 1, ['a', 'b', 'c']);
    const second = placeNodes(occupied, origin, 1, ['d', 'e', 'f']);
    const all = [...Object.values(first), ...Object.values(second)].map(cellKey);
    expect(new Set(all).size).toBe(6);
  });

  it('fills the nearest column first and spreads a hub across further columns', () => {
    const placed = placeNodes(new Set(), origin, 1, Array.from({ length: 120 }, (_, i) => `n${String(i).padStart(3, '0')}`));
    const columns = new Set(Object.values(placed).map((cell) => cell.c));
    expect(Math.min(...columns)).toBe(1);
    expect(columns.size).toBeGreaterThan(1);
  });
});
