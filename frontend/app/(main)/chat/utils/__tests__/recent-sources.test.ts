import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RECENT_SOURCES_MAX, readRecentSources, rememberRecentSources } from '../recent-sources';

const node = (id: string) => ({ id, name: `Node ${id}`, nodeType: 'record', connector: 'KB' });

beforeEach(() => window.localStorage.clear());
afterEach(() => vi.restoreAllMocks());

describe('recent sources', () => {
  it('keeps the last picks per person, newest first, without repeats', () => {
    rememberRecentSources('u1', [node('a')], 1);
    rememberRecentSources('u1', [node('b')], 2);
    rememberRecentSources('u1', [node('a')], 3);
    expect(readRecentSources('u1').map((n) => n.id)).toEqual(['a', 'b']);
    expect(readRecentSources('u2')).toEqual([]);
  });

  it(`forgets the oldest past ${RECENT_SOURCES_MAX}`, () => {
    for (let i = 0; i < RECENT_SOURCES_MAX + 2; i += 1) rememberRecentSources('u1', [node(`n${i}`)], i);
    const ids = readRecentSources('u1').map((n) => n.id);
    expect(ids).toHaveLength(RECENT_SOURCES_MAX);
    expect(ids[0]).toBe(`n${RECENT_SOURCES_MAX + 1}`);
  });

  it('works on, empty, where storage can not be used', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => rememberRecentSources('u1', [node('a')])).not.toThrow();
    expect(readRecentSources('u1')).toEqual([]);
  });

  it('ignores what it did not write', () => {
    window.localStorage.setItem('chat.mentionRecentSources:u1', '{"not":"a list"}');
    expect(readRecentSources('u1')).toEqual([]);
  });
});
