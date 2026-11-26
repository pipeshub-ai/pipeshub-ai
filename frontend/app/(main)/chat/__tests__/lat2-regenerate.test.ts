/**
 * LAT2 FR-1..FR-4: the web UI regenerates in one request on the existing
 * regenerate route; the server's first frame says whether the durable
 * harness took it.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient, streamSSEGet, streamSSERequest, type SSEEvent } from '@/lib/api';
import { StreamError } from '@/lib/api/stream-errors';
import { ChatApi } from '../api';
import { DurableAdmissionError } from '../harness-errors';
import { ANSWER, asOwner, completedRun, RUN_ID, SESSION_ID } from './durable-frames';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
  streamSSEGet: vi.fn(),
}));

const mockedPost = vi.mocked(apiClient.post);
const mockedStream = vi.mocked(streamSSERequest);
const mockedStreamGet = vi.mocked(streamSSEGet);

const TURN = '3f1c2a8e-9d0b-4c5e-8a7f-112233445566';

/** Today's regenerate: a bare greeting first, no run id, no SSE ids. */
function legacyRegenerate(): SSEEvent[] {
  const [, ...rest] = completedRun().map(({ id: _id, ...frame }) => frame);
  const greeting = { type: 'CUSTOM', name: 'conversation_created', value: { message: 'SSE connection established' } };
  return [{ event: 'CUSTOM', data: greeting }, ...rest];
}

function serve(frames: SSEEvent[], error?: Error): void {
  mockedStream.mockImplementation(async (_url, _body, options) => {
    frames.forEach((frame) => options.onEvent(frame));
    if (error) options.onError(error);
  });
}

const model = {
  modelKey: 'model-1',
  modelName: 'Model',
  modelFriendlyName: 'Model',
  filters: { apps: [], kb: [] },
  runId: TURN,
};

const regenerateAssistant = (callbacks: Parameters<typeof ChatApi.streamRegenerate>[2]) =>
  ChatApi.streamRegenerate(SESSION_ID, 'answer-9', callbacks, { ...model, chatMode: 'internal_search' });

const regenerateAgent = (callbacks: Parameters<typeof ChatApi.streamAgentRegenerate>[3]) =>
  ChatApi.streamAgentRegenerate('agent-1', SESSION_ID, 'answer-9', callbacks, { ...model, chatMode: 'auto' });

describe('LAT2: regenerate is one request', () => {
  beforeEach(() => {
    mockedPost.mockReset();
    mockedStream.mockReset();
    mockedStreamGet.mockReset();
  });

  it.each([
    ['assistant', regenerateAssistant, `/api/v1/conversations/${SESSION_ID}/message/answer-9/regenerate`, undefined],
    ['agent', regenerateAgent, `/api/v1/agents/agent-1/conversations/${SESSION_ID}/message/answer-9/regenerate`, 'agent-1'],
  ] as const)('FR-1: %s regenerate is one POST on the regenerate route; the run it took is followed from that response', async (_name, send, endpoint, agentId) => {
    serve(asOwner(completedRun()));
    const onDurableRunAccepted = vi.fn();
    const onChunk = vi.fn();
    const onComplete = vi.fn();
    await send({ onDurableRunAccepted, onChunk, onComplete });

    expect(mockedStream).toHaveBeenCalledTimes(1);
    expect(mockedStream).toHaveBeenCalledWith(endpoint, expect.objectContaining({ runId: TURN, protocol: 'agui' }), expect.any(Object));
    expect(mockedPost, 'no …/runs admission').not.toHaveBeenCalled();
    expect(mockedStreamGet).not.toHaveBeenCalled();
    expect(onDurableRunAccepted).toHaveBeenCalledWith({ ...(agentId ? { agentId } : {}), sessionId: SESSION_ID, runId: RUN_ID });
    expect(onChunk).toHaveBeenLastCalledWith(expect.objectContaining({ accumulated: ANSWER }));
    expect(onComplete).toHaveBeenCalledOnce();
  });

  it('FR-2: today’s loop answers on the same response; nothing else is sent', async () => {
    serve(legacyRegenerate());
    const onDurableRunAccepted = vi.fn();
    const onLegacyTurn = vi.fn();
    const onComplete = vi.fn();
    const onError = vi.fn();
    await regenerateAssistant({ onDurableRunAccepted, onLegacyTurn, onComplete, onError });

    expect(mockedStream).toHaveBeenCalledTimes(1);
    expect(mockedPost).not.toHaveBeenCalled();
    expect(onDurableRunAccepted).not.toHaveBeenCalled();
    expect(onLegacyTurn).toHaveBeenCalledOnce();
    expect(onComplete).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  it('FR-3: a dropped regenerate response is followed on the run stream after its last frame', async () => {
    const frames = asOwner(completedRun());
    const cut = 8;
    serve(frames.slice(0, cut), new StreamError('dropped'));
    mockedStreamGet.mockImplementation(async (_url, options) => {
      options.onOpen?.();
      frames.slice(cut).forEach((frame) => options.onEvent(frame));
    });
    const onChunk = vi.fn();
    const onComplete = vi.fn();
    const onError = vi.fn();
    await regenerateAgent({ onChunk, onComplete, onError });

    expect(mockedStreamGet).toHaveBeenCalledWith(
      `/api/v1/agents/agent-1/conversations/${SESSION_ID}/runs/${RUN_ID}/stream`,
      expect.objectContaining({ headers: { 'Last-Event-ID': frames[cut - 1]!.id } }),
    );
    expect(onChunk).toHaveBeenLastCalledWith(expect.objectContaining({ accumulated: ANSWER }));
    expect(onComplete).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  it('FR-4: a refusal says what happened in the reader’s language, from its code', async () => {
    serve([], new StreamError('Only the last answer', 409, 'HTTP_AGENT_HARNESS_REGENERATE_NOT_LAST'));
    const onError = vi.fn();
    await regenerateAssistant({ onError });

    const [error] = onError.mock.calls[0]!;
    expect(error).toBeInstanceOf(DurableAdmissionError);
    expect((error as DurableAdmissionError).code).toBe('HTTP_AGENT_HARNESS_REGENERATE_NOT_LAST');
  });

  it('FR-1: the durable admission path for regenerate is gone', () => {
    expect('regenerateDurably' in ChatApi).toBe(false);
  });
});
