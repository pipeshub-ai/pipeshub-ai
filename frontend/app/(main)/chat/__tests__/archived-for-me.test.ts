import { describe, it, expect, beforeEach, vi } from 'vitest';

const apiClient = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('@/lib/api', () => ({ apiClient }));

import { ChatApi, mapApiConversationToConversation } from '../api';
import { AgentsApi } from '../../agents/api';
import type { ConversationApiResponse } from '../types';

// A per-user archive leaves `isArchived` false and `archivedFor` is not selected, so a list row never
// says it is archived. The archive list's membership is the signal.
const row = (id: string) =>
  ({
    _id: id, title: id, createdAt: 'a', updatedAt: 'b', isShared: true, sharedWith: [],
    isArchived: false, status: 'complete', isOwner: false,
  }) as unknown as ConversationApiResponse;
const PAGINATION = { page: 1, limit: 20, totalCount: 1, totalPages: 1, hasNextPage: false, hasPrevPage: false };

beforeEach(() => vi.clearAllMocks());

describe('archivedForMe', () => {
  it('is not set on ordinary list rows', () => {
    expect(mapApiConversationToConversation(row('c1')).archivedForMe).toBeUndefined();
  });

  it('is set on every row of the archives list', async () => {
    apiClient.get.mockResolvedValue({ data: { conversations: [row('c1'), row('c2')], pagination: PAGINATION } });
    const { conversations } = await ChatApi.fetchArchivedConversations();
    expect(conversations.map((c) => c.archivedForMe)).toEqual([true, true]);
  });

  it('is set on archive search results', async () => {
    apiClient.get.mockResolvedValue({
      data: { conversations: [{ ...row('c1'), source: 'assistant' }], pagination: PAGINATION, summary: {} },
    });
    const { conversations } = await ChatApi.searchArchivedConversations({ search: 'x' });
    expect(conversations[0].archivedForMe).toBe(true);
  });

  it('is set on agent archive rows, grouped or per agent', async () => {
    apiClient.get.mockResolvedValueOnce({ data: { groups: [{ agentKey: 'a1', conversations: [row('c1')], pagination: PAGINATION }] } });
    const grouped = await AgentsApi.fetchAllAgentsArchivedConversations();
    expect(grouped.groups[0].conversations[0].archivedForMe).toBe(true);

    apiClient.get.mockResolvedValueOnce({ data: { conversations: [row('c2')], pagination: PAGINATION } });
    const perAgent = await AgentsApi.fetchAgentArchivedConversations('a1');
    expect(perAgent.conversations[0].archivedForMe).toBe(true);
  });
});
