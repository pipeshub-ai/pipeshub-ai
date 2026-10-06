import { describe, it, expect } from 'vitest';
import { hideAccessLost } from '../hide-access-lost';

const list = [{ id: 'a' }, { id: 'b' }];

describe('hideAccessLost', () => {
  it('drops a chat whose slot lost access', () => {
    const slots = { s1: { convId: 'a', accessLost: true }, s2: { convId: 'b', accessLost: false } };
    expect(hideAccessLost(list, slots, true)).toEqual([{ id: 'b' }]);
  });
  it('returns the same array when nothing is lost', () => {
    expect(hideAccessLost(list, { s1: { convId: 'a' } }, true)).toBe(list);
  });
  it('is a no-op with the flag off', () => {
    expect(hideAccessLost(list, { s1: { convId: 'a', accessLost: true } }, false)).toBe(list);
  });
  it('ignores a lost slot with no conversation id', () => {
    expect(hideAccessLost(list, { s1: { convId: null, accessLost: true } }, true)).toBe(list);
  });
});
