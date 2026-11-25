/**
 * LAT1 FE-1..FE-4, FE-6: the web UI sends every chat turn as one request on
 * the existing chat route; the server's first frame says whether the durable
 * harness took it.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient, streamSSEGet, streamSSERequest, type SSEEvent } from '@/lib/api';
import { StreamError } from '@/lib/api/stream-errors';
import { ChatApi } from '../api';
import { DurableAdmissionError } from '../harness-errors';
import { buildStreamChatRequestForSlot } from '../runtime';
import { ASSISTANT_CTX, useChatStore, type ProjectChatScope } from '../store';
import type { StreamChatRequest } from '../types';
import { ANSWER, asOwner, completedRun, RUN_ID, SESSION_ID } from './durable-frames';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
  streamSSEGet: vi.fn(),
}));

vi.mock('../utils/client-time', () => ({
  getClientTimezone: () => 'Asia/Kolkata',
  getClientCurrentTime: () => '2026-10-12T10:00:00+05:30',
}));

const mockedPost = vi.mocked(apiClient.post);
const mockedGet = vi.mocked(apiClient.get);
const mockedStream = vi.mocked(streamSSERequest);
const mockedStreamGet = vi.mocked(streamSSEGet);

const ownerTurn = (): SSEEvent[] => asOwner(completedRun());

/** Today's loop: no run id, no SSE ids. */
function legacyTurn(): SSEEvent[] {
  return completedRun().map(({ id: _id, ...frame }) => frame);
}

function serve(frames: SSEEvent[], error?: Error): void {
  mockedStream.mockImplementation(async (_url, _body, options) => {
    frames.forEach((frame) => options.onEvent(frame));
    if (error) options.onError(error);
  });
}

const request = (extra: Partial<StreamChatRequest> = {}): StreamChatRequest => ({
  query: 'Question',
  modelKey: 'model-1',
  modelName: 'Model',
  modelFriendlyName: 'Model',
  chatMode: 'internal_search',
  filters: { apps: [], kb: [] },
  runId: '3f1c2a8e-9d0b-4c5e-8a7f-112233445566',
  ...extra,
});

describe('LAT1: one request per chat turn', () => {
  beforeEach(() => {
    mockedPost.mockReset();
    mockedGet.mockReset();
    mockedStream.mockReset();
    mockedStreamGet.mockReset();
  });

  it.each([
    ['assistant internal_search', {}, '/api/v1/conversations/stream', 'internal_search'],
    ['assistant web_search', { chatMode: 'web_search' as const }, '/api/v1/conversations/stream', 'web_search'],
    ['assistant follow-up', { conversationId: 'c-1' }, '/api/v1/conversations/c-1/messages/stream', 'internal_search'],
    ['agent auto', { agentId: 'agent-1', chatMode: 'agent:auto' as const }, '/api/v1/agents/agent-1/conversations/stream', 'auto'],
    ['agent quick', { agentId: 'agent-1', chatMode: 'agent:quick' as const }, '/api/v1/agents/agent-1/conversations/stream', 'quick'],
    ['agent deep', { agentId: 'agent-1', chatMode: 'agent:deep' as const }, '/api/v1/agents/agent-1/conversations/stream', 'deep'],
  ])('FE-1: %s is one POST on the existing route; the run it took is followed from that response', async (_name, extra, endpoint, chatMode) => {
    serve(ownerTurn());
    const onDurableRunAccepted = vi.fn();
    const onComplete = vi.fn();
    const onChunk = vi.fn();
    await ChatApi.streamMessage(request(extra), { onDurableRunAccepted, onComplete, onChunk });

    expect(mockedStream).toHaveBeenCalledTimes(1);
    expect(mockedStream).toHaveBeenCalledWith(
      endpoint,
      expect.objectContaining({ query: 'Question', chatMode, protocol: 'agui', runId: request().runId }),
      expect.any(Object),
    );
    expect(mockedPost, 'no …/runs admission').not.toHaveBeenCalled();
    expect(mockedStreamGet, 'nothing to reattach').not.toHaveBeenCalled();
    expect(onDurableRunAccepted).toHaveBeenCalledWith({
      ...('agentId' in extra ? { agentId: 'agent-1' } : {}),
      sessionId: SESSION_ID,
      runId: RUN_ID,
    });
    expect(onChunk).toHaveBeenLastCalledWith(expect.objectContaining({ accumulated: ANSWER }));
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it.each([
    ['agent planExecute', { agentId: 'agent-1', chatMode: 'agent:planExecute' as const }],
    ['Universal Agent Mode', { chatMode: 'agent' as const }],
  ])('FE-2: %s answered by today’s loop on the same response; nothing else is sent', async (_name, extra) => {
    serve(legacyTurn());
    const onDurableRunAccepted = vi.fn();
    const onComplete = vi.fn();
    const onError = vi.fn();
    await ChatApi.streamMessage(request(extra), { onDurableRunAccepted, onComplete, onError });

    expect(mockedStream).toHaveBeenCalledTimes(1);
    expect(mockedPost).not.toHaveBeenCalled();
    expect(mockedStreamGet).not.toHaveBeenCalled();
    expect(onDurableRunAccepted).not.toHaveBeenCalled();
    expect(onComplete).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  it('FE-3: a dropped response is followed on the run stream after its last frame, by the same handler', async () => {
    const frames = ownerTurn();
    const cut = 8;
    serve(frames.slice(0, cut), new StreamError('dropped'));
    mockedStreamGet.mockImplementation(async (_url, options) => {
      options.onOpen?.();
      frames.slice(cut).forEach((frame) => options.onEvent(frame));
    });
    const onChunk = vi.fn();
    const onComplete = vi.fn();
    const onError = vi.fn();
    await ChatApi.streamMessage(request(), { onChunk, onComplete, onError });

    expect(mockedStreamGet).toHaveBeenCalledTimes(1);
    expect(mockedStreamGet).toHaveBeenCalledWith(
      `/api/v1/conversations/${SESSION_ID}/runs/${RUN_ID}/stream`,
      expect.objectContaining({ headers: { 'Last-Event-ID': frames[cut - 1]!.id } }),
    );
    expect(onChunk).toHaveBeenLastCalledWith(expect.objectContaining({ accumulated: ANSWER }));
    expect(onComplete).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  it('FE-4: a refusal says what happened in the reader’s language, from its code', async () => {
    serve([], new StreamError('Too many agent runs', 429, 'HTTP_AGENT_HARNESS_QUOTA_EXCEEDED'));
    const onError = vi.fn();
    await ChatApi.streamMessage(request(), { onError });

    const [error] = onError.mock.calls[0]!;
    expect(error).toBeInstanceOf(DurableAdmissionError);
    expect((error as DurableAdmissionError).code).toBe('HTTP_AGENT_HARNESS_QUOTA_EXCEEDED');
    expect(mockedPost).not.toHaveBeenCalled();
  });

  it('FE-4: any other failure keeps the stream’s own message', async () => {
    serve([], new StreamError('PipesHub is busy right now.', 503));
    const onError = vi.fn();
    await ChatApi.streamMessage(request(), { onError });
    expect(onError).toHaveBeenCalledWith(expect.objectContaining({ message: 'PipesHub is busy right now.' }));
  });

  it('FE-6: a new chat in a project is the same request; the server reads the project from the body', async () => {
    serve(legacyTurn());
    await ChatApi.streamMessage(request({ projectId: 'p-1' }), {});
    expect(mockedStream).toHaveBeenCalledTimes(1);
    const [, body] = mockedStream.mock.calls[0]!;
    expect(body).toMatchObject({ projectId: 'p-1' });
    expect(mockedPost).not.toHaveBeenCalled();
  });
});

const MODEL = { modelKey: 'model-1', modelName: 'Model', modelFriendlyName: 'Model' };

const PROJECT_SCOPE: ProjectChatScope = {
  projectId: 'p-1',
  connectors: [{ id: 'app-1', label: 'Slack', connectorKind: 'SLACK' }],
  knowledgeCollectionRows: [],
  knowledgeDefaults: { apps: ['app-1'], kb: [] },
  toolGroups: [],
  mcpGroups: [],
  toolCatalogFullNames: [],
};

type Turn = { agentId?: string; conversationId?: string; projectId?: string };

/** What the composer sends for a slot in that state: the request it builds, posted by the chat API. */
async function sendFromComposer(turn: Turn) {
  const store = useChatStore.getState();
  if (turn.projectId && !turn.agentId) store.setProjectScope(PROJECT_SCOPE);
  const slotId = store.createSlot(turn.conversationId ?? null);
  store.updateSlot(slotId, {
    ...(turn.projectId ? { projectId: turn.projectId } : {}),
    ...(turn.agentId ? { threadAgentId: turn.agentId } : {}),
  });
  const built = buildStreamChatRequestForSlot(slotId, 'Question');
  await ChatApi.streamMessage({ ...built!, runId: RUN }, {});
  const [endpoint, body] = mockedStream.mock.calls[0]!;
  // As serialised: a key whose value is undefined is not sent.
  return { built, endpoint, body: JSON.parse(JSON.stringify(body)) as unknown };
}

const RUN = request().runId;
const SENT = {
  query: 'Question',
  ...MODEL,
  timezone: 'Asia/Kolkata',
  currentTime: '2026-10-12T10:00:00+05:30',
  runId: RUN,
  protocol: 'agui',
};
const ASSISTANT_BODY = {
  ...SENT,
  chatMode: 'agent',
  agentCapabilities: { internalSearch: true, webSearch: true, deepSearch: false },
};
// A project turn sends that project's own capabilities (main #4615's per-project defaults), not the composer's.
const PROJECT_KNOWLEDGE = {
  agentCapabilities: { internalSearch: true, webSearch: true },
  filters: { apps: ['app-1'], kb: [] },
  appliedFilters: { apps: [{ id: 'app-1', name: 'Slack', nodeType: 'app', connector: 'SLACK', mentioned: false }], kb: [] },
};
const AGENT_BODY = {
  ...SENT,
  chatMode: 'quick',
  filters: { apps: [], kb: [] },
  appliedFilters: { apps: [], kb: [] },
  agentCapabilities: { internalSearch: true, webSearch: true },
};

describe('CPD10-F1: a chat turn names its project only for the conversation it creates', () => {
  const initial = useChatStore.getState().settings;

  beforeEach(() => {
    mockedStream.mockReset();
    serve(legacyTurn());
    useChatStore.setState({
      slots: {},
      projectScope: null,
      projectKnowledgeScope: null,
      settings: {
        ...initial,
        queryMode: 'agent',
        agentCapabilities: { internalSearch: true, webSearch: true, deepSearch: false },
        selectedModels: { [ASSISTANT_CTX]: MODEL, 'agent-1': MODEL },
        reasoningEffort: {},
        availableModels: {},
      },
    });
  });

  it.each<[string, Turn, string, Record<string, unknown>]>([
    [
      'assistant, first turn in a project',
      { projectId: 'p-1' },
      '/api/v1/conversations/stream',
      { ...ASSISTANT_BODY, ...PROJECT_KNOWLEDGE, projectId: 'p-1' },
    ],
    [
      'assistant, later turn in a project conversation',
      { projectId: 'p-1', conversationId: 'c-1' },
      '/api/v1/conversations/c-1/messages/stream',
      { ...ASSISTANT_BODY, ...PROJECT_KNOWLEDGE, conversationId: 'c-1' },
    ],
    ['assistant, no project', {}, '/api/v1/conversations/stream', ASSISTANT_BODY],
    [
      'agent, first turn in a project',
      { agentId: 'agent-1', projectId: 'p-1' },
      '/api/v1/agents/agent-1/conversations/stream',
      { ...AGENT_BODY, projectId: 'p-1' },
    ],
    [
      'agent, later turn in a project conversation',
      { agentId: 'agent-1', projectId: 'p-1', conversationId: 'c-1' },
      '/api/v1/agents/agent-1/conversations/c-1/messages/stream',
      AGENT_BODY,
    ],
    ['agent, no project', { agentId: 'agent-1' }, '/api/v1/agents/agent-1/conversations/stream', AGENT_BODY],
  ])('%s', async (_name, turn, expectedEndpoint, expectedBody) => {
    const { built, endpoint, body } = await sendFromComposer(turn);

    expect(mockedStream).toHaveBeenCalledTimes(1);
    expect(endpoint).toBe(expectedEndpoint);
    expect(body).toStrictEqual(expectedBody);
    expect(built, 'the composer decides nothing about project scope').not.toHaveProperty('projectScoped');
  });
});
