import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import {
  allowChatDraftWrites,
  areChatDraftWritesBlocked,
  discardAllChatDrafts,
} from '../chat-drafts';

beforeEach(() => {
  window.localStorage.clear();
  allowChatDraftWrites();
});

afterEach(() => {
  vi.restoreAllMocks();
  allowChatDraftWrites();
});

describe('discardAllChatDrafts', () => {
  it('removes every saved draft, whoever it belonged to', () => {
    window.localStorage.setItem('pipeshub-chat-draft:u1:-:conv-1', '{"text":"a","savedAt":1}');
    window.localStorage.setItem('pipeshub-chat-draft:u2:agent-1:new', '{"text":"b","savedAt":1}');

    discardAllChatDrafts();

    expect(window.localStorage.length).toBe(0);
  });

  it('leaves everything else in the browser alone', () => {
    window.localStorage.setItem('pipeshub-chat-draft:u1:-:conv-1', 'x');
    window.localStorage.setItem('pipeshub-agent-capabilities:a1', '{"web":true}');
    window.localStorage.setItem('theme', 'dark');

    discardAllChatDrafts();

    expect(window.localStorage.getItem('pipeshub-agent-capabilities:a1')).toBe('{"web":true}');
    expect(window.localStorage.getItem('theme')).toBe('dark');
    expect(window.localStorage.getItem('pipeshub-chat-draft:u1:-:conv-1')).toBeNull();
  });

  it('does nothing harmful when there are no drafts', () => {
    expect(() => discardAllChatDrafts()).not.toThrow();
    expect(window.localStorage.length).toBe(0);
  });

  it('does not throw when the browser blocks storage', () => {
    vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });

    expect(() => discardAllChatDrafts()).not.toThrow();
  });

  it('still blocks new drafts when storage cannot be reached', () => {
    vi.spyOn(window, 'localStorage', 'get').mockImplementation(() => {
      throw new DOMException('denied', 'SecurityError');
    });

    discardAllChatDrafts();

    expect(areChatDraftWritesBlocked()).toBe(true);
  });
});

describe('blocking new drafts', () => {
  it('allows drafts by default', () => {
    expect(areChatDraftWritesBlocked()).toBe(false);
  });

  it('blocks drafts after they are discarded, until a new session starts', () => {
    discardAllChatDrafts();
    expect(areChatDraftWritesBlocked()).toBe(true);

    allowChatDraftWrites();
    expect(areChatDraftWritesBlocked()).toBe(false);
  });
});
