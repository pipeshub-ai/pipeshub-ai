import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  DRAFT_MAX_AGE_MS,
  DRAFT_MAX_CHARS,
  DRAFT_MAX_COUNT,
  buildDraftKey,
  clearDraft,
  loadDraft,
  saveDraft,
} from '../composer-draft';
import { allowChatDraftWrites, discardAllChatDrafts } from '@/lib/storage/chat-drafts';

beforeEach(() => {
  window.localStorage.clear();
  allowChatDraftWrites();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('buildDraftKey', () => {
  it('has no key until the user is known', () => {
    expect(buildDraftKey({ userId: null, convId: 'c1' })).toBeNull();
    expect(buildDraftKey({ userId: undefined, convId: 'c1' })).toBeNull();
    expect(buildDraftKey({ userId: '', convId: 'c1' })).toBeNull();
  });

  it('gives each conversation its own key', () => {
    expect(buildDraftKey({ userId: 'u1', convId: 'c1' })).not.toBe(
      buildDraftKey({ userId: 'u1', convId: 'c2' }),
    );
  });

  it('gives each user their own key for the same conversation', () => {
    expect(buildDraftKey({ userId: 'u1', convId: 'c1' })).not.toBe(
      buildDraftKey({ userId: 'u2', convId: 'c1' }),
    );
  });

  it('keeps a new chat apart from any saved conversation and from a new agent chat', () => {
    const plain = buildDraftKey({ userId: 'u1', convId: null });
    expect(plain).not.toBe(buildDraftKey({ userId: 'u1', convId: 'c1' }));
    expect(plain).not.toBe(buildDraftKey({ userId: 'u1', convId: null, agentId: 'agent-1' }));
  });

  it('treats a missing, null and empty conversation or agent the same', () => {
    expect(buildDraftKey({ userId: 'u1', convId: undefined })).toBe(
      buildDraftKey({ userId: 'u1', convId: null, agentId: null }),
    );
    expect(buildDraftKey({ userId: 'u1', convId: '' })).toBe(
      buildDraftKey({ userId: 'u1', convId: null, agentId: '' }),
    );
  });
});

describe('saving and loading', () => {
  it('returns what was saved', () => {
    saveDraft('pipeshub-chat-draft:a', 'half a question');

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('half a question');
  });

  it('keeps line breaks and surrounding spaces exactly', () => {
    saveDraft('pipeshub-chat-draft:a', '  line one\n\nline two  ');

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('  line one\n\nline two  ');
  });

  it('returns nothing for a draft that was never saved', () => {
    expect(loadDraft('pipeshub-chat-draft:missing')).toBe('');
  });

  it('replaces the previous text', () => {
    saveDraft('pipeshub-chat-draft:a', 'first');
    saveDraft('pipeshub-chat-draft:a', 'second');

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('second');
  });

  it('keeps drafts for different keys apart', () => {
    saveDraft('pipeshub-chat-draft:a', 'one');
    saveDraft('pipeshub-chat-draft:b', 'two');

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('one');
    expect(loadDraft('pipeshub-chat-draft:b')).toBe('two');
  });

  it('removes the draft when the text is emptied', () => {
    saveDraft('pipeshub-chat-draft:a', 'text');
    saveDraft('pipeshub-chat-draft:a', '');

    expect(window.localStorage.getItem('pipeshub-chat-draft:a')).toBeNull();
  });

  it('does not keep a draft made only of spaces and line breaks', () => {
    saveDraft('pipeshub-chat-draft:a', 'text');
    saveDraft('pipeshub-chat-draft:a', '  \n\t ');

    expect(window.localStorage.getItem('pipeshub-chat-draft:a')).toBeNull();
  });

  it('clearDraft removes only that draft', () => {
    saveDraft('pipeshub-chat-draft:a', 'one');
    saveDraft('pipeshub-chat-draft:b', 'two');

    clearDraft('pipeshub-chat-draft:a');

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('');
    expect(loadDraft('pipeshub-chat-draft:b')).toBe('two');
  });

  it('keeps text that looks like JSON or markup as plain text', () => {
    saveDraft('pipeshub-chat-draft:a', '{"text":"x"} <script>alert(1)</script>');

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('{"text":"x"} <script>alert(1)</script>');
  });

  it('does not store text longer than the limit, and keeps the earlier draft', () => {
    saveDraft('pipeshub-chat-draft:a', 'short');
    saveDraft('pipeshub-chat-draft:a', 'x'.repeat(DRAFT_MAX_CHARS + 1));

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('short');
  });

  it('stores text exactly at the limit', () => {
    const text = 'x'.repeat(DRAFT_MAX_CHARS);
    saveDraft('pipeshub-chat-draft:a', text);

    expect(loadDraft('pipeshub-chat-draft:a')).toBe(text);
  });
});

describe('old and broken drafts', () => {
  it('drops a draft that has not been touched for too long, and removes it', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    saveDraft('pipeshub-chat-draft:a', 'old thought');

    vi.setSystemTime(Date.now() + DRAFT_MAX_AGE_MS + 1000);

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('');
    expect(window.localStorage.getItem('pipeshub-chat-draft:a')).toBeNull();
  });

  it('still returns a draft just inside the age limit', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    saveDraft('pipeshub-chat-draft:a', 'recent thought');

    vi.setSystemTime(Date.now() + DRAFT_MAX_AGE_MS - 1000);

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('recent thought');
  });

  it.each([
    ['not JSON', 'just words'],
    ['JSON of the wrong shape', '{"text":5,"savedAt":"x"}'],
    ['a JSON string', '"hello"'],
    ['null', 'null'],
    ['an array', '[1,2]'],
  ])('treats an unreadable stored value (%s) as no draft', (_label, raw) => {
    window.localStorage.setItem('pipeshub-chat-draft:a', raw);

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('');
  });

  it('replaces a broken value when a new draft is saved over it', () => {
    window.localStorage.setItem('pipeshub-chat-draft:a', 'garbage');

    saveDraft('pipeshub-chat-draft:a', 'fresh');

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('fresh');
  });
});

describe('storage clean-up', () => {
  it('keeps only the most recent drafts once there are too many', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    for (let i = 0; i < DRAFT_MAX_COUNT + 5; i += 1) {
      saveDraft(`pipeshub-chat-draft:${i}`, `draft ${i}`);
      vi.setSystemTime(Date.now() + 1000);
    }

    const kept = Object.keys(window.localStorage).filter((k) => k.startsWith('pipeshub-chat-draft:'));
    expect(kept).toHaveLength(DRAFT_MAX_COUNT);
    expect(loadDraft('pipeshub-chat-draft:0')).toBe('');
    expect(loadDraft('pipeshub-chat-draft:4')).toBe('');
    expect(loadDraft(`pipeshub-chat-draft:${DRAFT_MAX_COUNT + 4}`)).toBe(`draft ${DRAFT_MAX_COUNT + 4}`);
  });

  it('removes expired drafts of other conversations when saving', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    saveDraft('pipeshub-chat-draft:old', 'forgotten');

    vi.setSystemTime(Date.now() + DRAFT_MAX_AGE_MS + 1000);
    saveDraft('pipeshub-chat-draft:new', 'today');

    expect(window.localStorage.getItem('pipeshub-chat-draft:old')).toBeNull();
    expect(loadDraft('pipeshub-chat-draft:new')).toBe('today');
  });

  it('never touches other things kept in the browser', () => {
    window.localStorage.setItem('pipeshub-agent-capabilities:a1', '{"web":true}');
    window.localStorage.setItem('theme', 'dark');
    for (let i = 0; i < DRAFT_MAX_COUNT + 10; i += 1) saveDraft(`pipeshub-chat-draft:${i}`, 'x');

    expect(window.localStorage.getItem('pipeshub-agent-capabilities:a1')).toBe('{"web":true}');
    expect(window.localStorage.getItem('theme')).toBe('dark');
  });
});

describe('when the browser will not store anything', () => {
  it('does not throw when reading is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('blocked');
    });

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('');
  });

  it('does not throw when the storage is full', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('quota', 'QuotaExceededError');
    });

    expect(() => saveDraft('pipeshub-chat-draft:a', 'text')).not.toThrow();
  });

  it('does not throw when removing is blocked', () => {
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('blocked');
    });

    expect(() => clearDraft('pipeshub-chat-draft:a')).not.toThrow();
    expect(() => saveDraft('pipeshub-chat-draft:a', '')).not.toThrow();
  });

  it('does not throw when the storage object itself cannot be reached', () => {
    vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('');
    expect(() => saveDraft('pipeshub-chat-draft:a', 'text')).not.toThrow();
    expect(() => clearDraft('pipeshub-chat-draft:a')).not.toThrow();
  });
});

describe('after a deliberate sign-out', () => {
  it('refuses to save a draft, so leaving the page cannot bring one back', () => {
    saveDraft('pipeshub-chat-draft:a', 'before sign-out');
    discardAllChatDrafts();

    saveDraft('pipeshub-chat-draft:a', 'typed while the page unloads');

    expect(window.localStorage.getItem('pipeshub-chat-draft:a')).toBeNull();
    expect(loadDraft('pipeshub-chat-draft:a')).toBe('');
  });

  it('saves again once a new session starts', () => {
    discardAllChatDrafts();
    allowChatDraftWrites();

    saveDraft('pipeshub-chat-draft:a', 'new session');

    expect(loadDraft('pipeshub-chat-draft:a')).toBe('new session');
  });
});
