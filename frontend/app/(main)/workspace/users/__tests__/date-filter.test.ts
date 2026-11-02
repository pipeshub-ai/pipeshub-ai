import { describe, expect, it } from 'vitest';
import { dateFilterToRange } from '../date-filter';

const local = (y: number, m: number, d: number) => String(new Date(y, m - 1, d).getTime());

describe('dateFilterToRange', () => {
  it('"on" covers the whole local day', () => {
    expect(dateFilterToRange('2026-10-05', '2026-10-05', 'on')).toEqual({
      from: local(2026, 10, 5),
      to: local(2026, 10, 6),
    });
  });

  it('"before" and "after" leave the chosen day out', () => {
    expect(dateFilterToRange(undefined, '2026-10-05', 'before')).toEqual({ to: local(2026, 10, 5) });
    expect(dateFilterToRange('2026-10-05', undefined, 'after')).toEqual({ from: local(2026, 10, 6) });
  });

  it('"between" includes both ends, across a month boundary', () => {
    expect(dateFilterToRange('2026-09-28', '2026-10-31', 'between')).toEqual({
      from: local(2026, 9, 28),
      to: local(2026, 11, 1),
    });
  });

  it('reads the date part of a value that carries a time', () => {
    expect(dateFilterToRange('2026-10-05T18:30', undefined, 'after')).toEqual({ from: local(2026, 10, 6) });
  });

  it('no dates means no bounds', () => {
    expect(dateFilterToRange(undefined, undefined, 'between')).toEqual({});
    expect(dateFilterToRange(undefined, undefined, undefined)).toEqual({});
  });
});
