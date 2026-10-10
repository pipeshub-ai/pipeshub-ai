import { describe, it, expect, afterEach, vi } from 'vitest';
import { clearDraft, loadDraft, saveDraft } from '../draft-storage';

afterEach(() => {
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe('draft storage', () => {
  it('keeps a draft per conversation and clears it', () => {
    saveDraft('c1', 'hello');
    saveDraft('c2', 'other');
    expect(loadDraft('c1')).toBe('hello');
    clearDraft('c1');
    expect(loadDraft('c1')).toBeNull();
    expect(loadDraft('c2')).toBe('other');
  });

  it('ignores empty drafts and ids', () => {
    saveDraft('c1', '');
    saveDraft('', 'x');
    expect(loadDraft('c1')).toBeNull();
    clearDraft('');
  });

  it('never throws when storage is full or blocked', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota');
    });
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    expect(() => saveDraft('c1', 'hello')).not.toThrow();
    expect(loadDraft('c1')).toBeNull();
    expect(() => clearDraft('c1')).not.toThrow();
  });
});
