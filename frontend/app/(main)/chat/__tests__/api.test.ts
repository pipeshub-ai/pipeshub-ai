import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient, streamSSERequest } from '@/lib/api';
import { ChatApi } from '../api';
import { CHAT_ATTACHMENT_UPLOAD_TIMEOUT_MS, type StreamChatRequest } from '../types';

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

const mockedGet = vi.mocked(apiClient.get);
const mockedPost = vi.mocked(apiClient.post);
const mockedPatch = vi.mocked(apiClient.patch);

describe('ChatApi.fetchAvailableLlms', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it('returns the models array on a well-formed response', async () => {
    const models = [{ modelKey: 'k1', modelName: 'gpt-5', provider: 'openai' }];
    mockedGet.mockResolvedValueOnce({ data: { status: 'success', models, message: '' } });
    const result = await ChatApi.fetchAvailableLlms();
    expect(result).toEqual(models);
  });

  it('returns an empty array when the response body is null', async () => {
    mockedGet.mockResolvedValueOnce({ data: null });
    const result = await ChatApi.fetchAvailableLlms();
    expect(result).toEqual([]);
  });

  it('returns an empty array when the response body is undefined', async () => {
    mockedGet.mockResolvedValueOnce({ data: undefined });
    const result = await ChatApi.fetchAvailableLlms();
    expect(result).toEqual([]);
  });

  it('returns an empty array when models is missing', async () => {
    mockedGet.mockResolvedValueOnce({ data: { status: 'success', message: '' } });
    const result = await ChatApi.fetchAvailableLlms();
    expect(result).toEqual([]);
  });

  it('returns an empty array when models is a malformed non-array value', async () => {
    mockedGet.mockResolvedValueOnce({ data: { status: 'success', models: { oops: true }, message: '' } });
    const result = await ChatApi.fetchAvailableLlms();
    expect(result).toEqual([]);
  });
});

describe('ChatApi.cancelStream', () => {
  beforeEach(() => {
    mockedPost.mockReset();
  });

  it('posts to the assistant cancel endpoint with the runId when no agentId is given', async () => {
    mockedPost.mockResolvedValueOnce({ data: { cancelled: true } });

    const result = await ChatApi.cancelStream('conv-1', 'run-123');

    expect(mockedPost).toHaveBeenCalledWith(
      '/api/v1/conversations/conv-1/cancel',
      { runId: 'run-123' },
      { suppressErrorToast: true },
    );
    expect(result).toEqual({ cancelled: true });
  });

  it('posts to the agent-scoped cancel endpoint when agentId is given', async () => {
    mockedPost.mockResolvedValueOnce({ data: { cancelled: false } });

    const result = await ChatApi.cancelStream('conv-1', 'run-123', 'agent-42');

    expect(mockedPost).toHaveBeenCalledWith(
      '/api/v1/agents/agent-42/conversations/conv-1/cancel',
      { runId: 'run-123' },
      { suppressErrorToast: true },
    );
    expect(result).toEqual({ cancelled: false });
  });

  it('falls back to the assistant endpoint when agentId is null', async () => {
    mockedPost.mockResolvedValueOnce({ data: { cancelled: true } });

    await ChatApi.cancelStream('conv-1', 'run-123', null);

    expect(mockedPost).toHaveBeenCalledWith(
      '/api/v1/conversations/conv-1/cancel',
      { runId: 'run-123' },
      { suppressErrorToast: true },
    );
  });
});

describe('ChatApi.streamMessage — answering a tool approval card', () => {
  const answer = { approvalId: 'ap-1', decision: 'allow_once' as const };
  const base: StreamChatRequest = {
    query: 'Allow once: create_issue on Jira',
    modelKey: 'k',
    modelName: 'm',
    modelFriendlyName: 'M',
    chatMode: 'agent',
    filters: { apps: [], kb: [] },
    conversationId: 'conv-1',
  };
  const sentBody = () => vi.mocked(streamSSERequest).mock.calls.at(-1)?.[1] as Record<string, unknown>;

  beforeEach(() => {
    vi.mocked(streamSSERequest).mockReset();
    vi.mocked(streamSSERequest).mockResolvedValue(undefined as never);
  });

  it('sends the answer in an agent chat', async () => {
    await ChatApi.streamMessage({ ...base, agentId: 'agent-1', toolApproval: answer }, {});
    expect(sentBody().toolApproval).toEqual(answer);
  });

  it('sends the answer in an assistant chat', async () => {
    await ChatApi.streamMessage({ ...base, toolApproval: answer }, {});
    expect(sentBody().toolApproval).toEqual(answer);
  });

  it('sends nothing when there is no answer', async () => {
    await ChatApi.streamMessage({ ...base, agentId: 'agent-1' }, {});
    expect(sentBody()).not.toHaveProperty('toolApproval');
  });
});

describe('ChatApi stream bodies — no model resolved', () => {
  const noModel = { modelKey: '', modelName: '', modelFriendlyName: '' };
  const base: StreamChatRequest = {
    query: 'hello',
    ...noModel,
    chatMode: 'quick',
    filters: { apps: [], kb: [] },
  };
  const sentBody = () => vi.mocked(streamSSERequest).mock.calls.at(-1)?.[1] as Record<string, unknown>;
  const expectNoModelFields = () => {
    const body = sentBody();
    expect(body).not.toHaveProperty('modelKey');
    expect(body).not.toHaveProperty('modelName');
    expect(body).not.toHaveProperty('modelFriendlyName');
  };

  beforeEach(() => {
    vi.mocked(streamSSERequest).mockReset();
    vi.mocked(streamSSERequest).mockResolvedValue(undefined as never);
  });

  it('leaves the model out of an assistant message so the server uses its default', async () => {
    await ChatApi.streamMessage(base, {});
    expectNoModelFields();
  });

  it('leaves the model out of an agent message', async () => {
    await ChatApi.streamMessage({ ...base, agentId: 'agent-1' }, {});
    expectNoModelFields();
  });

  it('leaves the model out of a regenerate', async () => {
    await ChatApi.streamRegenerate('conv-1', 'msg-1', {}, { ...noModel, chatMode: 'quick', filters: { apps: [], kb: [] } });
    expectNoModelFields();
  });

  it('leaves the model out of an agent regenerate', async () => {
    await ChatApi.streamAgentRegenerate('agent-1', 'conv-1', 'msg-1', {}, { ...noModel, chatMode: 'auto' } as never);
    expectNoModelFields();
  });

  it('still sends a chosen model, falling back to its name for the friendly name', async () => {
    await ChatApi.streamMessage({ ...base, modelKey: 'k1', modelName: 'gpt-5', modelFriendlyName: '' }, {});
    expect(sentBody()).toMatchObject({ modelKey: 'k1', modelName: 'gpt-5', modelFriendlyName: 'gpt-5' });
  });
});

describe('ChatApi.uploadAttachments', () => {
  beforeEach(() => {
    mockedPost.mockReset();
  });

  it('waits longer than the 90s client default, since the server describes images first', async () => {
    mockedPost.mockResolvedValueOnce({ data: { attachments: [{ recordId: 'rec-1' }] } });
    const file = new File(['%PDF-1.4'], 'report.pdf', { type: 'application/pdf' });

    const refs = await ChatApi.uploadAttachments([file], { conversationId: 'conv-1' });

    expect(refs).toEqual([{ recordId: 'rec-1' }]);
    const config = mockedPost.mock.calls[0][2] as { timeout?: number };
    expect(config.timeout).toBe(CHAT_ATTACHMENT_UPLOAD_TIMEOUT_MS);
    expect(CHAT_ATTACHMENT_UPLOAD_TIMEOUT_MS).toBeGreaterThan(90_000);
  });
});

describe('ChatApi.setConversationPinned', () => {
  beforeEach(() => {
    mockedPatch.mockReset();
    mockedPatch.mockResolvedValue({ data: {} });
  });

  it('pins with PATCH .../pin', async () => {
    await ChatApi.setConversationPinned('conv-1', true);

    expect(mockedPatch).toHaveBeenCalledTimes(1);
    expect(mockedPatch).toHaveBeenCalledWith('/api/v1/conversations/conv-1/pin');
  });

  it('unpins with PATCH .../unpin', async () => {
    await ChatApi.setConversationPinned('conv-1', false);

    expect(mockedPatch).toHaveBeenCalledWith('/api/v1/conversations/conv-1/unpin');
  });

  it('keeps an odd id inside its path segment', async () => {
    await ChatApi.setConversationPinned('a/b?c', true);

    expect(mockedPatch).toHaveBeenCalledWith('/api/v1/conversations/a%2Fb%3Fc/pin');
  });

  it('lets a failed request reach the caller, which has to roll the row back', async () => {
    mockedPatch.mockRejectedValueOnce(new Error('404'));

    await expect(ChatApi.setConversationPinned('conv-1', true)).rejects.toThrow('404');
  });
});

describe('ChatApi.fetchConversations — pin flag', () => {
  const row = (id: string, extra: Record<string, unknown> = {}) => ({
    _id: id,
    title: id,
    createdAt: '2026-01-01T00:00:00.000Z',
    updatedAt: '2026-01-01T00:00:00.000Z',
    isShared: false,
    sharedWith: [],
    lastActivityAt: 1,
    isOwner: true,
    ...extra,
  });

  beforeEach(() => mockedGet.mockReset());

  it('marks a pinned chat and leaves the others without the field', async () => {
    mockedGet.mockResolvedValueOnce({
      data: {
        conversations: [row('a', { isPinned: true }), row('b')],
        source: 'owned',
        pagination: { page: 1, limit: 10, totalCount: 2, totalPages: 1, hasNextPage: false, hasPrevPage: false },
      },
    });

    const { conversations } = await ChatApi.fetchConversations(1, 10, { source: 'owned' });

    expect(conversations[0].isPinned).toBe(true);
    expect('isPinned' in conversations[1]).toBe(false);
  });

  it('never carries a false pin, whatever the server sent', async () => {
    mockedGet.mockResolvedValueOnce({
      data: {
        conversations: [row('a', { isPinned: false })],
        source: 'owned',
        pagination: { page: 1, limit: 10, totalCount: 1, totalPages: 1, hasNextPage: false, hasPrevPage: false },
      },
    });

    const { conversations } = await ChatApi.fetchConversations(1, 10, { source: 'owned' });

    expect('isPinned' in conversations[0]).toBe(false);
  });
});

describe('mapApiConversationToConversation', () => {
  it('keeps the agent a shared agent conversation belongs to, and leaves plain chats without one', async () => {
    const { mapApiConversationToConversation } = await import('../api');
    const base = { _id: 'c1', title: 'T', createdAt: 'a', updatedAt: 'b', isShared: true, isOwner: false } as never;
    expect(mapApiConversationToConversation({ ...(base as object), agentKey: 'agent-7' } as never).agentKey).toBe('agent-7');
    expect(mapApiConversationToConversation(base).agentKey).toBeUndefined();
  });

  it('keeps the project a chat belongs to, so its menu offers the right move and removal', async () => {
    const { mapApiConversationToConversation } = await import('../api');
    const base = { _id: 'c1', title: 'T', createdAt: 'a', updatedAt: 'b', isShared: false, isOwner: true } as never;
    const inProject = mapApiConversationToConversation({
      ...(base as object),
      projectId: 'p1',
      projectVisibility: 'private',
    } as never);
    expect(inProject.projectId).toBe('p1');
    expect(inProject.projectVisibility).toBe('private');
    expect('projectId' in mapApiConversationToConversation(base)).toBe(false);
  });
});
