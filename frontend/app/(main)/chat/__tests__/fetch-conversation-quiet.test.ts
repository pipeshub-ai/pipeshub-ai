import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { isGoneError } from '@/lib/api/api-error';
import { ChatApi } from '../api';
import { AgentsApi } from '../../agents/api';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn() },
  streamSSERequest: vi.fn(),
}));

const mockedGet = vi.mocked(apiClient.get);
const detail = { data: { conversation: { id: 'c1', messages: [], access: {} }, filters: {}, meta: {} } };

beforeEach(() => {
  mockedGet.mockReset();
  mockedGet.mockResolvedValue(detail as never);
});

describe('opening a chat that is gone', () => {
  it('ChatApi.fetchConversation leaves 404/403 to the caller only when asked', async () => {
    await ChatApi.fetchConversation('c1', 1, 20, { quietWhenGone: true });
    expect(mockedGet.mock.calls[0][1]).toMatchObject({ suppressErrorToast: isGoneError });
    await ChatApi.fetchConversation('c1');
    expect(mockedGet.mock.calls[1][1]).not.toHaveProperty('suppressErrorToast');
  });

  it('AgentsApi.fetchAgentConversation does the same', async () => {
    await AgentsApi.fetchAgentConversation('a1', 'c1', { quietWhenGone: true }).catch(() => undefined);
    expect(mockedGet.mock.calls[0][1]).toMatchObject({ suppressErrorToast: isGoneError });
    await AgentsApi.fetchAgentConversation('a1', 'c1').catch(() => undefined);
    expect(mockedGet.mock.calls[1][1]).not.toHaveProperty('suppressErrorToast');
  });
});
