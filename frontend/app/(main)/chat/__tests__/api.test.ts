import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient, streamSSEGet, streamSSERequest } from '@/lib/api';
import { ChatApi } from '../api';
import { useFeatureFlagsStore } from '@/lib/store/feature-flags-store';
import { ANSWER, completedRun } from './durable-frames';

type FeatureState = {
  flags: Record<string, boolean>;
  agentHarness: { useHarness: boolean; resumeInFlightRuns: boolean; harnessOn: boolean } | null;
};

const featureState = vi.hoisted(
  (): FeatureState => ({ flags: {}, agentHarness: null }),
);

// Real selectors, fake state: the chat API must follow the server's decision.
vi.mock('@/lib/store/feature-flags-store', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/store/feature-flags-store')>()),
  useFeatureFlagsStore: {
    getState: () => featureState,
    setState: (next: Partial<FeatureState>) => Object.assign(featureState, next),
  },
}));
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
  streamSSEGet: vi.fn(),
}));

const mockedGet = vi.mocked(apiClient.get);
const mockedPost = vi.mocked(apiClient.post);
const mockedStream = vi.mocked(streamSSERequest);
const mockedStreamGet = vi.mocked(streamSSEGet);
const mockedPatch = vi.mocked(apiClient.patch);

/** The run's stream answers with a whole completed run. */
function serveCompletedRun(): void {
  mockedStreamGet.mockImplementation(async (_url, options) => {
    options.onOpen?.();
    completedRun().forEach((frame) => options.onEvent(frame));
  });
}

describe('ChatApi.fetchOrgLlmCatalog', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPost.mockReset();
    useFeatureFlagsStore.setState({ flags: {}, agentHarness: null });
  });

  it('returns the models array on a well-formed response', async () => {
    const models = [{ modelKey: 'k1', modelName: 'gpt-5', provider: 'openai' }];
    mockedGet.mockResolvedValueOnce({ data: { status: 'success', models, message: '' } });
    const { models: result } = await ChatApi.fetchOrgLlmCatalog();
    expect(result).toEqual(models);
  });

  it('returns an empty array when the response body is null', async () => {
    mockedGet.mockResolvedValueOnce({ data: null });
    const { models: result } = await ChatApi.fetchOrgLlmCatalog();
    expect(result).toEqual([]);
  });

  it('returns an empty array when the response body is undefined', async () => {
    mockedGet.mockResolvedValueOnce({ data: undefined });
    const { models: result } = await ChatApi.fetchOrgLlmCatalog();
    expect(result).toEqual([]);
  });

  it('returns an empty array when models is missing', async () => {
    mockedGet.mockResolvedValueOnce({ data: { status: 'success', message: '' } });
    const { models: result } = await ChatApi.fetchOrgLlmCatalog();
    expect(result).toEqual([]);
  });

  it('returns an empty array when models is a malformed non-array value', async () => {
    mockedGet.mockResolvedValueOnce({ data: { status: 'success', models: { oops: true }, message: '' } });
    const { models: result } = await ChatApi.fetchOrgLlmCatalog();
    expect(result).toEqual([]);
  });
});

/** Admission refusals are shown in the reply, never as a generic toast (UX3 AR-1). */
const ADMISSION_CONFIG = expect.objectContaining({ suppressErrorToast: true });

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

describe('ChatApi durable agent transport', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPost.mockReset();
    mockedStreamGet.mockReset();
    useFeatureFlagsStore.setState({
      flags: {},
      agentHarness: { useHarness: true, resumeInFlightRuns: true, harnessOn: true },
    });
  });

  // F-4: with the harness off, agent and assistant chat never touch the durable transport.
  it.each([
    ['agent chat', { agentId: 'agent-1', chatMode: 'agent:auto' }, '/api/v1/agents/agent-1/conversations/stream'],
    ['assistant chat', { chatMode: 'quick' }, '/api/v1/conversations/stream'],
  ])('uses the legacy stream for %s when the rollout has no harness', async (_name, extra, endpoint) => {
    mockedStream.mockReset();
    useFeatureFlagsStore.setState({ flags: { ENABLE_AGENT_HARNESS: true }, agentHarness: null });

    await ChatApi.streamMessage(
      {
        query: 'Question',
        modelKey: 'model-1',
        modelName: 'Model',
        modelFriendlyName: 'Model',
        filters: { apps: [], kb: [] },
        ...extra,
      },
      {},
    );

    expect(mockedStream).toHaveBeenCalledWith(endpoint, expect.any(Object), expect.any(Object));
    expect(mockedPost).not.toHaveBeenCalled();
    expect(mockedStreamGet).not.toHaveBeenCalled();
    expect(mockedGet).not.toHaveBeenCalled();
  });

  it('ignores the raw Labs flag and follows the server rollout decision', async () => {
    mockedStream.mockReset();
    useFeatureFlagsStore.setState({
      flags: { ENABLE_AGENT_HARNESS: true },
      agentHarness: { useHarness: false, resumeInFlightRuns: true, harnessOn: false },
    });

    await ChatApi.streamMessage(
      {
        query: 'Question',
        modelKey: 'model-1',
        modelName: 'Model',
        modelFriendlyName: 'Model',
        chatMode: 'quick',
        filters: { apps: [], kb: [] },
      },
      {},
    );

    expect(mockedPost).not.toHaveBeenCalled();
    expect(mockedStream).toHaveBeenCalledWith(
      '/api/v1/conversations/stream',
      expect.any(Object),
      expect.any(Object),
    );
  });

  it('reattaches to an existing run without submitting another turn', async () => {
    serveCompletedRun();
    const onChunk = vi.fn();
    const onComplete = vi.fn();
    const onDurableRunAccepted = vi.fn();

    await ChatApi.resumeDurableAgentRun('agent-2', 'session-2', 'run-2', {
      onChunk,
      onComplete,
      onDurableRunAccepted,
    });

    expect(mockedPost).not.toHaveBeenCalled();
    expect(mockedStreamGet).toHaveBeenCalledWith(
      '/api/v1/agents/agent-2/conversations/session-2/runs/run-2/stream',
      expect.any(Object),
    );
    expect(onDurableRunAccepted).toHaveBeenCalledWith({
      agentId: 'agent-2',
      sessionId: 'session-2',
      runId: 'run-2',
    });
    expect(onChunk).toHaveBeenLastCalledWith(expect.objectContaining({ accumulated: ANSWER }));
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it.each([
    ['a new chat linked to a project', { projectId: 'project-1' }],
    ['a later turn in a project conversation', { conversationId: 'chat-4' }],
  ])('sends %s as one request, asking the server nothing first', async (_name, extra) => {
    mockedStream.mockReset();

    await ChatApi.streamMessage(
      {
        query: 'Question',
        modelKey: 'model-1',
        modelName: 'Model',
        modelFriendlyName: 'Model',
        chatMode: 'internal_search',
        filters: { apps: [], kb: [] },
        ...extra,
      },
      {},
    );

    expect(mockedPost).not.toHaveBeenCalled();
    expect(mockedStreamGet).not.toHaveBeenCalled();
    expect(mockedStream).toHaveBeenCalledTimes(1);
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

describe('ChatApi.listWorkspaceVersions', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("raises no global toast: a shared chat's participant is refused (404) on open, and callers show nothing", async () => {
    mockedGet.mockResolvedValueOnce({ data: { workspaceId: 'ws:conv-1', head: 0, versions: [] } });

    await ChatApi.listWorkspaceVersions({ sessionId: 'conv-1' }, { limit: 50 });

    const config = mockedGet.mock.calls[0][1] as { params?: unknown; suppressErrorToast?: boolean };
    expect(config).toMatchObject({ params: { limit: 50 }, suppressErrorToast: true });
  });
});

describe('ChatApi.uploadWorkspaceFiles', () => {
  beforeEach(() => {
    mockedPost.mockReset();
  });

  it('sends multipart, not the client default JSON that turns each file into {}', async () => {
    mockedPost.mockResolvedValueOnce({ data: { revision: 2, paths: ['uploads/a.csv'], replaced: [] } });
    const file = new File(['a,b\n'], 'a.csv', { type: 'text/csv' });

    await ChatApi.uploadWorkspaceFiles(
      { agentId: 'agent-1', sessionId: 'conv-1' },
      { files: [file], folder: 'uploads', baseRevision: 1, idempotencyKey: 'k1', replace: false },
    );

    const [, body, config] = mockedPost.mock.calls[0] as [string, FormData, { headers?: Record<string, unknown> }];
    expect(body).toBeInstanceOf(FormData);
    expect(config.headers).toHaveProperty('Content-Type', undefined);
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
