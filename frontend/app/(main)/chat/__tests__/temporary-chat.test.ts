import { describe, it, expect, beforeEach, vi } from 'vitest';
import { useChatStore } from '../store';
import { ChatApi } from '../api';
import { apiClient, streamSSERequest } from '@/lib/api';
import type { StreamChatRequest } from '../types';
import {
  clearTemporaryChatSession,
  readTemporaryChatSession,
  writeTemporaryChatSession,
} from '../utils/temporary-chat-session';

vi.mock('@/lib/api', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
  streamSSERequest: vi.fn(),
}));

describe('temporary chat store and API', () => {
  beforeEach(() => {
    useChatStore.setState({
      slots: {},
      activeSlotId: null,
      pendingConversations: {},
    } as never);
    vi.mocked(apiClient.delete).mockReset();
    vi.mocked(streamSSERequest).mockReset();
  });

  it('locks the toggle after a message is present', () => {
    const slotId = useChatStore.getState().createSlot(null);
    useChatStore.getState().setTemporaryChat(slotId, true);
    expect(useChatStore.getState().slots[slotId].isTemporaryChat).toBe(true);

    useChatStore.getState().updateSlot(slotId, {
      messages: [{ id: 'm1', role: 'user', content: 'hi' } as never],
    });
    useChatStore.getState().setTemporaryChat(slotId, false);
    expect(useChatStore.getState().slots[slotId].isTemporaryChat).toBe(true);
  });

  it('streamMessage sends temporary on a new chat only', async () => {
    vi.mocked(streamSSERequest).mockResolvedValue(undefined);
    const request: StreamChatRequest = {
      query: 'hello',
      modelKey: 'k',
      modelName: 'n',
      modelFriendlyName: 'f',
      chatMode: 'quick',
      filters: { apps: [], kb: [] },
      temporary: true,
    };

    await ChatApi.streamMessage(request, {} as never);

    expect(streamSSERequest).toHaveBeenCalledOnce();
    const [, payload] = vi.mocked(streamSSERequest).mock.calls[0];
    expect(payload).toMatchObject({ query: 'hello', temporary: true });
    expect(payload).not.toHaveProperty('conversationId');
  });

  it('purgeTemporaryConversation hits the hard-delete route', async () => {
    vi.mocked(apiClient.delete).mockResolvedValue({} as never);
    await ChatApi.purgeTemporaryConversation('abc123');
    expect(apiClient.delete).toHaveBeenCalledWith(
      '/api/v1/conversations/abc123/temporary',
      { suppressErrorToast: true },
    );
  });

  it('a new slot is a normal chat until the toggle is on', () => {
    const slotId = useChatStore.getState().createSlot(null);
    expect(useChatStore.getState().slots[slotId].isTemporaryChat).toBe(false);
    expect(useChatStore.getState().slots[slotId].isTemp).toBe(true);
  });

  it('purgeTemporaryConversationOnExit uses a keepalive DELETE', () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal('fetch', fetchMock);
    ChatApi.purgeTemporaryConversationOnExit('abc123', 'token-1');
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/conversations/abc123/temporary',
      {
        method: 'DELETE',
        headers: { Authorization: 'Bearer token-1' },
        keepalive: true,
      },
    );
    vi.unstubAllGlobals();
  });

  it('keeps a temporary chat id in sessionStorage across refresh', () => {
    sessionStorage.clear();
    writeTemporaryChatSession(null);
    expect(readTemporaryChatSession()).toEqual({ conversationId: null });
    writeTemporaryChatSession('abc123');
    expect(readTemporaryChatSession()).toEqual({ conversationId: 'abc123' });
    clearTemporaryChatSession();
    expect(readTemporaryChatSession()).toBeNull();
  });
});
