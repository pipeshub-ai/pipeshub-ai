import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('@/lib/api', () => ({ apiClient: { get: vi.fn() } }));

import { useChatStore, selectSlotIsOwner } from '../store';
import { mapApiConversationToConversation } from '../api';
import type { ConversationApiResponse } from '../types';

beforeEach(() => useChatStore.getState().reset());

describe('slot access', () => {
  it('a new chat starts as a solo owner, an existing one has no access until loaded', () => {
    const fresh = useChatStore.getState().createSlot(null);
    const existing = useChatStore.getState().createSlot('conv-1');
    const { slots } = useChatStore.getState();
    expect(slots[fresh]).toMatchObject({ activeRun: null, rev: null, accessLost: false });
    expect(selectSlotIsOwner(slots[fresh])).toBe(true);
    expect(slots[fresh].access).toMatchObject({ canSend: true, isCollaborative: false });
    expect(selectSlotIsOwner(slots[existing])).toBeNull();
    expect(selectSlotIsOwner(undefined)).toBeNull();
  });
});

describe('mapApiConversationToConversation', () => {
  const row = {
    _id: 'c1', title: 't', createdAt: 'a', updatedAt: 'b', isShared: true, sharedWith: [],
    lastActivityAt: 1, status: 'complete', isOwner: false,
  } as unknown as ConversationApiResponse;

  it('leaves rows unchanged when the server sends no collaboration fields', () => {
    const mapped = mapApiConversationToConversation(row);
    expect(mapped).not.toHaveProperty('access');
    expect(mapped).not.toHaveProperty('unreadCount');
    expect(mapped).not.toHaveProperty('collaboratorCount');
  });

  it('carries access, unreadCount and collaboratorCount', () => {
    const mapped = mapApiConversationToConversation({
      ...row,
      access: { isOwner: false, accessLevel: 'write' },
      unreadCount: 3,
      collaboratorCount: 2,
    });
    expect(mapped.access).toMatchObject({ role: 'write', isOwner: false });
    expect(mapped.unreadCount).toBe(3);
    expect(mapped.collaboratorCount).toBe(2);
  });
});
