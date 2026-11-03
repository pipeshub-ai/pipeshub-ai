import { describe, it, expect } from 'vitest';
import { asArray, asRecord, lower } from '../safe';

describe('safe', () => {
  it('asArray turns a missing list into an empty one and keeps a real one', () => {
    expect(asArray(undefined)).toEqual([]);
    expect(asArray(null)).toEqual([]);
    const list = [1, 2];
    expect(asArray(list)).toBe(list);
  });

  it('asRecord turns a missing map into an empty one and keeps a real one', () => {
    expect(asRecord(null)).toEqual({});
    expect(asRecord(undefined)).toEqual({});
    const map = { a: 1 };
    expect(asRecord(map)).toBe(map);
  });

  it('lower lowercases a string and treats a missing one as empty', () => {
    expect(lower(undefined)).toBe('');
    expect(lower(null)).toBe('');
    expect(lower('AbC')).toBe('abc');
  });
});
