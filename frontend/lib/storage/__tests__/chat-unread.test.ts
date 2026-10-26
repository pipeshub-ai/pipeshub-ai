import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  CHAT_UNREAD_KEY_PREFIX,
  UNREAD_MAX_AGE_MS,
  UNREAD_MAX_COUNT,
  allowChatUnreadWrites,
  chatUnreadKey,
  discardAllChatUnread,
  forgetUnreadConversation,
  loadUnreadConversationIds,
  rememberUnreadConversation,
} from '../chat-unread';

const NOW = new Date('2026-10-16T10:00:00Z').getTime();
const DAY = 24 * 60 * 60 * 1000;

function memoryStorage(): { backing: Map<string, string>; storage: Storage } {
  const backing = new Map<string, string>();
  const storage = {
    getItem: (key: string) => (backing.has(key) ? (backing.get(key) as string) : null),
    setItem: (key: string, value: string) => void backing.set(key, String(value)),
    removeItem: (key: string) => void backing.delete(key),
    clear: () => backing.clear(),
    key: (index: number) => Array.from(backing.keys())[index] ?? null,
    get length() {
      return backing.size;
    },
  } as unknown as Storage;
  return { backing, storage };
}

let backing: Map<string, string>;

function useStorage(storage: Storage) {
  Object.defineProperty(window, 'localStorage', { configurable: true, value: storage });
}

const keyOf = (userId: string) => `${CHAT_UNREAD_KEY_PREFIX}${userId}`;
const ids = (userId: string) => [...loadUnreadConversationIds(userId)].sort();

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  const made = memoryStorage();
  backing = made.backing;
  useStorage(made.storage);
  allowChatUnreadWrites();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('where unread chats are kept', () => {
  it('uses one key per signed-in user', () => {
    expect(chatUnreadKey('user-1')).toBe('pipeshub-chat-unread:user-1');
    expect(chatUnreadKey('user-2')).not.toBe(chatUnreadKey('user-1'));
  });

  it.each([null, undefined, ''])('has no key when nobody is signed in (%s)', (userId) => {
    expect(chatUnreadKey(userId)).toBeNull();
  });
});

describe('remembering and forgetting', () => {
  it('keeps a chat unread across a reload', () => {
    rememberUnreadConversation('user-1', 'conv-1');

    expect(ids('user-1')).toEqual(['conv-1']);
  });

  it('keeps several chats', () => {
    rememberUnreadConversation('user-1', 'conv-1');
    rememberUnreadConversation('user-1', 'conv-2');

    expect(ids('user-1')).toEqual(['conv-1', 'conv-2']);
  });

  it('forgets one chat and keeps the others', () => {
    rememberUnreadConversation('user-1', 'conv-1');
    rememberUnreadConversation('user-1', 'conv-2');

    forgetUnreadConversation('user-1', 'conv-1');

    expect(ids('user-1')).toEqual(['conv-2']);
  });

  it('removes the stored entry altogether once the last chat is read', () => {
    rememberUnreadConversation('user-1', 'conv-1');

    forgetUnreadConversation('user-1', 'conv-1');

    expect(backing.has(keyOf('user-1'))).toBe(false);
  });

  it('forgetting a chat that was never unread changes nothing', () => {
    rememberUnreadConversation('user-1', 'conv-1');

    forgetUnreadConversation('user-1', 'conv-other');

    expect(ids('user-1')).toEqual(['conv-1']);
  });

  it('writes nothing for a user who has no unread chats and reads one that is absent', () => {
    forgetUnreadConversation('user-1', 'conv-1');

    expect(backing.size).toBe(0);
    expect(ids('user-1')).toEqual([]);
  });
});

describe('a shared browser', () => {
  it("never shows one user's unread chats to another", () => {
    rememberUnreadConversation('user-1', 'conv-1');

    expect(ids('user-2')).toEqual([]);
  });

  it("keeps each user's chats apart", () => {
    rememberUnreadConversation('user-1', 'conv-1');
    rememberUnreadConversation('user-2', 'conv-2');

    forgetUnreadConversation('user-1', 'conv-1');

    expect(ids('user-1')).toEqual([]);
    expect(ids('user-2')).toEqual(['conv-2']);
  });

  it.each([null, undefined, ''])('stores and loads nothing when nobody is signed in (%s)', (userId) => {
    rememberUnreadConversation(userId, 'conv-1');

    expect(backing.size).toBe(0);
    expect(loadUnreadConversationIds(userId).size).toBe(0);
  });
});

describe('two tabs open at once', () => {
  it("does not undo another tab's newly unread chat", () => {
    rememberUnreadConversation('user-1', 'conv-1');
    // Another tab flags a second chat in between.
    backing.set(keyOf('user-1'), JSON.stringify({ 'conv-1': NOW, 'conv-from-other-tab': NOW }));

    rememberUnreadConversation('user-1', 'conv-2');

    expect(ids('user-1')).toEqual(['conv-1', 'conv-2', 'conv-from-other-tab']);
  });

  it("does not bring back a chat the other tab already read", () => {
    rememberUnreadConversation('user-1', 'conv-1');
    rememberUnreadConversation('user-1', 'conv-2');
    // Another tab read conv-1.
    backing.set(keyOf('user-1'), JSON.stringify({ 'conv-2': NOW }));

    rememberUnreadConversation('user-1', 'conv-3');

    expect(ids('user-1')).toEqual(['conv-2', 'conv-3']);
  });
});

describe('limits', () => {
  it('drops a chat nobody opened for 30 days', () => {
    rememberUnreadConversation('user-1', 'conv-old');
    vi.setSystemTime(NOW + UNREAD_MAX_AGE_MS + DAY);

    expect(ids('user-1')).toEqual([]);
  });

  it('keeps a chat that is just inside the age limit', () => {
    rememberUnreadConversation('user-1', 'conv-1');
    vi.setSystemTime(NOW + UNREAD_MAX_AGE_MS - DAY);

    expect(ids('user-1')).toEqual(['conv-1']);
  });

  it('drops an expired chat from storage on the next write', () => {
    rememberUnreadConversation('user-1', 'conv-old');
    vi.setSystemTime(NOW + UNREAD_MAX_AGE_MS + DAY);

    rememberUnreadConversation('user-1', 'conv-new');

    expect(Object.keys(JSON.parse(backing.get(keyOf('user-1')) as string))).toEqual(['conv-new']);
  });

  it('keeps only the newest chats once there are too many', () => {
    for (let i = 0; i < UNREAD_MAX_COUNT + 5; i += 1) {
      vi.setSystemTime(NOW + i * 1000);
      rememberUnreadConversation('user-1', `conv-${i}`);
    }

    const kept = loadUnreadConversationIds('user-1');
    expect(kept.size).toBe(UNREAD_MAX_COUNT);
    expect(kept.has('conv-0')).toBe(false);
    expect(kept.has('conv-4')).toBe(false);
    expect(kept.has('conv-5')).toBe(true);
    expect(kept.has(`conv-${UNREAD_MAX_COUNT + 4}`)).toBe(true);
  });

  it('refreshes how long a chat is kept when it is flagged again', () => {
    rememberUnreadConversation('user-1', 'conv-1');
    vi.setSystemTime(NOW + UNREAD_MAX_AGE_MS - DAY);
    rememberUnreadConversation('user-1', 'conv-1');
    vi.setSystemTime(NOW + UNREAD_MAX_AGE_MS + DAY);

    expect(ids('user-1')).toEqual(['conv-1']);
  });
});

describe('storage that is unreadable or unavailable', () => {
  it.each([
    ['not JSON', 'not json'],
    ['an array', '["conv-1"]'],
    ['a number', '5'],
    ['null', 'null'],
    ['an empty string', ''],
  ])('treats %s as nothing saved', (_label, raw) => {
    backing.set(keyOf('user-1'), raw);

    expect(ids('user-1')).toEqual([]);
  });

  it('ignores entries whose time is not a number but keeps the good ones', () => {
    backing.set(keyOf('user-1'), JSON.stringify({ 'conv-bad': 'yesterday', 'conv-nan': null, 'conv-ok': NOW }));

    expect(ids('user-1')).toEqual(['conv-ok']);
  });

  it('recovers by overwriting a corrupt entry on the next write', () => {
    backing.set(keyOf('user-1'), 'not json');

    rememberUnreadConversation('user-1', 'conv-1');

    expect(ids('user-1')).toEqual(['conv-1']);
  });

  it('does not throw when reading throws', () => {
    useStorage({
      ...memoryStorage().storage,
      getItem: () => {
        throw new Error('blocked');
      },
    } as Storage);

    expect(() => loadUnreadConversationIds('user-1')).not.toThrow();
    expect(loadUnreadConversationIds('user-1').size).toBe(0);
  });

  it('does not throw when writing throws, for example when the quota is full', () => {
    useStorage({
      ...memoryStorage().storage,
      getItem: () => null,
      setItem: () => {
        throw new Error('quota');
      },
    } as Storage);

    expect(() => rememberUnreadConversation('user-1', 'conv-1')).not.toThrow();
    expect(() => forgetUnreadConversation('user-1', 'conv-1')).not.toThrow();
  });

  it('does not throw when the storage object itself cannot be reached', () => {
    Object.defineProperty(window, 'localStorage', {
      configurable: true,
      get() {
        throw new Error('SecurityError');
      },
    });

    expect(() => rememberUnreadConversation('user-1', 'conv-1')).not.toThrow();
    expect(loadUnreadConversationIds('user-1').size).toBe(0);
    expect(() => discardAllChatUnread()).not.toThrow();
  });
});

describe('signing out on purpose', () => {
  it('removes every user’s unread chats', () => {
    rememberUnreadConversation('user-1', 'conv-1');
    rememberUnreadConversation('user-2', 'conv-2');

    discardAllChatUnread();

    expect(backing.size).toBe(0);
  });

  it('leaves everything else in storage alone', () => {
    backing.set('pipeshub-chat-draft:user-1:-:conv-1', '{"text":"hello"}');
    backing.set('theme', 'dark');
    rememberUnreadConversation('user-1', 'conv-1');

    discardAllChatUnread();

    expect([...backing.keys()].sort()).toEqual(['pipeshub-chat-draft:user-1:-:conv-1', 'theme']);
  });

  it('refuses to save an unread chat afterwards, such as one that finishes after sign-out', () => {
    discardAllChatUnread();

    rememberUnreadConversation('user-1', 'conv-late');

    expect(backing.size).toBe(0);
  });

  it('also refuses to change what is saved afterwards', () => {
    rememberUnreadConversation('user-1', 'conv-1');
    discardAllChatUnread();
    backing.set(keyOf('user-1'), JSON.stringify({ 'conv-1': NOW }));

    forgetUnreadConversation('user-1', 'conv-1');

    expect(ids('user-1')).toEqual(['conv-1']);
  });

  it('saves again once a new session starts', () => {
    discardAllChatUnread();
    allowChatUnreadWrites();

    rememberUnreadConversation('user-1', 'conv-1');

    expect(ids('user-1')).toEqual(['conv-1']);
  });
});
