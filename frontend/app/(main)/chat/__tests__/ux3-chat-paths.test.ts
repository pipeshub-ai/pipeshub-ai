/**
 * UX3 in the chat's transport and state: sub-agent and unknown-effect cards
 * (UE-9, CA-5), refused admissions (AR-1..AR-3), Regenerate on the durable
 * path (RG-1, RG-2), Stop during admission (ST-8), the queue and a run that
 * never started (QD-1, QD-4), and the engine card after a reload (EC-1).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient, streamSSEGet, streamSSERequest, type SSEEvent } from '@/lib/api';
import { i18n } from '@/lib/i18n';
import { STREAM_ERROR_MESSAGES, StreamError } from '@/lib/api/stream-errors';
import { createAGUIEventHandler } from '../agui-event-handler';
import { ChatApi, type StreamMessageCallbacks } from '../api';
import { approvalStreamCallbacks, decideApproval, withApprovalRequested } from '../approvals';
import { useChatStore } from '../store';
import type { PendingApproval, SSEApprovalRequestedEvent, StreamChatRequest } from '../types';

type FeatureState = { flags: Record<string, boolean>; agentHarness: { useHarness: boolean; resumeInFlightRuns: boolean; harnessOn: boolean } | null };
const featureState = vi.hoisted((): FeatureState => ({ flags: {}, agentHarness: null }));

vi.mock('@/lib/store/feature-flags-store', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/store/feature-flags-store')>()),
  useFeatureFlagsStore: {
    getState: () => featureState,
    setState: (next: Partial<FeatureState>) => Object.assign(featureState, next),
  },
}));

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
  streamSSEGet: vi.fn(),
}));

const post = vi.mocked(apiClient.post);
const get = vi.mocked(apiClient.get);
const legacyStream = vi.mocked(streamSSERequest);
const runStream = vi.mocked(streamSSEGet);

const request = (over: Partial<StreamChatRequest> = {}): StreamChatRequest =>
  ({
    query: 'Question',
    conversationId: 'session-1',
    agentId: 'agent-1',
    modelKey: 'model-1',
    modelName: 'gpt-5',
    modelFriendlyName: 'GPT-5',
    chatMode: 'agent:auto',
    filters: { apps: [], kb: [] },
    ...over,
  }) as StreamChatRequest;

const frame = (type: string, fields: Record<string, unknown>): SSEEvent => ({ event: type, data: { type, ...fields } });

beforeEach(async () => {
  post.mockReset();
  get.mockReset();
  legacyStream.mockReset();
  runStream.mockReset();
  featureState.agentHarness = { useHarness: true, resumeInFlightRuns: true, harnessOn: true };
  await i18n.changeLanguage('en-US');
});

describe('UE-9, CA-5: unknown effects and sub-agent approvals reach the chat', () => {
  it('an unknown_effect frame becomes a card of its own kind; its resolution closes it', () => {
    const onApprovalRequested = vi.fn();
    const onApprovalResolved = vi.fn();
    const handle = createAGUIEventHandler({ onApprovalRequested, onApprovalResolved } as StreamMessageCallbacks);
    handle(frame('CUSTOM', { name: 'unknown_effect', value: { interactionId: 'ix.unknown.t1.0', toolCallId: 'c1', toolName: 'slack__send_message', argsSummary: '#sales' } }));
    handle(frame('CUSTOM', { name: 'unknown_effect_resolved', value: { interactionId: 'ix.unknown.t1.0', decision: 'answered' } }));
    expect(onApprovalRequested).toHaveBeenCalledWith(expect.objectContaining({ kind: 'unknown_effect', interactionId: 'ix.unknown.t1.0', args: '' }));
    expect(onApprovalResolved).toHaveBeenCalledWith({ interactionId: 'ix.unknown.t1.0', decision: 'answered' });
  });

  it('a child’s interaction with the same id as the parent’s is a card of its own', () => {
    const asked: SSEApprovalRequestedEvent = { interactionId: 'ix.approve.t1.0', toolCallId: 'c', toolName: 't', args: '{}', argsSummary: null };
    let list: PendingApproval[] = withApprovalRequested([], asked);
    list = withApprovalRequested(list, { ...asked, childRunId: 'child-1', childRole: 'research' });
    list = withApprovalRequested(list, { ...asked, childRunId: 'child-1', childRole: 'research' });
    expect(list.map((a) => a.childRunId ?? 'root')).toEqual(['root', 'child-1']);
  });

  it('a child’s decision is posted to the child run, in the same session', async () => {
    const slotId = useChatStore.getState().createSlot('session-1');
    useChatStore.getState().updateSlot(slotId, { durableRun: { agentId: 'agent-1', sessionId: 'session-1', runId: 'run-1' } });
    approvalStreamCallbacks(slotId).onApprovalRequested?.({
      interactionId: 'ix.unknown.t1.0', toolCallId: 'c', toolName: 't', args: '', argsSummary: null,
      kind: 'unknown_effect', childRunId: 'child-1', childRole: 'research',
    });
    post.mockResolvedValue({ data: {} });
    await decideApproval(slotId, { interactionId: 'ix.unknown.t1.0', childRunId: 'child-1', runId: 'run-1' }, 'answered', 'once');
    expect(post).toHaveBeenCalledWith(
      '/api/v1/agents/agent-1/conversations/session-1/runs/child-1/interactions/ix.unknown.t1.0/decision',
      { decision: 'answered', idempotencyKey: 'ix.unknown.t1.0:answered' },
      { suppressErrorToast: true },
    );
    expect(useChatStore.getState().slots[slotId]?.pendingApprovals?.[0]?.status).toBe('answered');
  });
});

describe('AR: a refused admission says what happened, in the reader’s language', () => {
  // LAT1: the turn is one request on the chat route; the refusal arrives as that request's error.
  const refused = (status: number, code: string) => {
    legacyStream.mockImplementation(async (_url, _body, options) => {
      options.onError(new StreamError('server text in English', status, code));
    });
  };

  it('AR-1: a refusal reaches the reply as translated text, never as a toast', async () => {
    refused(429, 'HTTP_AGENT_HARNESS_QUOTA_EXCEEDED');
    const onError = vi.fn();
    await ChatApi.streamMessage(request(), { onError });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0]?.[0].message).toBe(i18n.t('chatStream.admission.quota'));
    expect(post).not.toHaveBeenCalled();
  });

  it('AR-1: a read-only viewer is told how to ask anyway', async () => {
    refused(403, 'HTTP_AGENT_HARNESS_VIEWER_READ_ONLY');
    const onError = vi.fn();
    await ChatApi.streamMessage(request(), { onError });
    expect(onError.mock.calls[0]?.[0].message).toMatch(/Continue privately/);
  });

  it('AR-2: today’s loop answers on the same route; the server falls back, not the browser', async () => {
    legacyStream.mockResolvedValue(undefined);
    await ChatApi.streamMessage(request(), { onError: vi.fn() });
    expect(legacyStream).toHaveBeenCalledTimes(1);
    expect(legacyStream).toHaveBeenCalledWith(
      '/api/v1/agents/agent-1/conversations/session-1/messages/stream',
      expect.any(Object),
      expect.any(Object),
    );
  });

  it('AR-3: a request that reached nobody: the stream’s readable network message, sent once', async () => {
    legacyStream.mockImplementation(async (_url, _body, options) => {
      options.onError(new StreamError(STREAM_ERROR_MESSAGES.offline));
    });
    const onError = vi.fn();
    await ChatApi.streamMessage(request(), { onError });
    expect(legacyStream).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0]?.[0].message).toBe(STREAM_ERROR_MESSAGES.offline);
  });
});

describe('RG: Regenerate takes the path a new turn takes', () => {
  it('RG-1, RG-2 (LAT2): one request on the regenerate route, durable or not, project or not; the server routes it', async () => {
    legacyStream.mockResolvedValue(undefined);
    const model = { modelKey: 'model-1', modelName: 'gpt-5', chatMode: 'auto' as const, filters: { apps: [], kb: [] } };
    await ChatApi.streamAgentRegenerate('agent-1', 'session-1', 'answer-9', {}, model);
    featureState.agentHarness = { useHarness: false, resumeInFlightRuns: true, harnessOn: false };
    await ChatApi.streamAgentRegenerate('agent-1', 'session-1', 'answer-9', {}, model);
    expect(post, 'no …/runs admission').not.toHaveBeenCalled();
    expect(legacyStream.mock.calls.map(([url]) => url)).toEqual([
      '/api/v1/agents/agent-1/conversations/session-1/message/answer-9/regenerate',
      '/api/v1/agents/agent-1/conversations/session-1/message/answer-9/regenerate',
    ]);
  });

  it('PS: an agent turn in a project is one request; the server keeps it on today’s loop', async () => {
    legacyStream.mockResolvedValue(undefined);
    await ChatApi.streamMessage(request({ projectId: 'p1' }), {});
    expect(post).not.toHaveBeenCalled();
    expect(legacyStream).toHaveBeenCalledTimes(1);
  });
});

describe('ST-8: Stop pressed while the turn is being admitted', () => {
  it('an aborted request follows nothing (the slot stops the run its first frame names: LAT1 FE-5)', async () => {
    const controller = new AbortController();
    legacyStream.mockImplementation(async () => {
      controller.abort();
    });
    const onDurableRunAccepted = vi.fn();
    await ChatApi.streamMessage(request(), { signal: controller.signal, onDurableRunAccepted });
    expect(onDurableRunAccepted).not.toHaveBeenCalled();
    expect(runStream).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
});

describe('QD: a queued run is never silent', () => {
  it('QD-1: the queued state reads "Waiting to start…"', () => {
    const onStatus = vi.fn();
    createAGUIEventHandler({ onStatus } as StreamMessageCallbacks)(frame('STATE_SNAPSHOT', { snapshot: { status: 'queued' } }));
    expect(onStatus).toHaveBeenCalledWith({ status: 'queued', message: i18n.t('chatStream.queued') });
  });

  it('QD-4: a run no worker started ends with words a person can act on', () => {
    const onError = vi.fn();
    createAGUIEventHandler({ onError } as StreamMessageCallbacks)(
      frame('RUN_ERROR', { code: 'not_started', message: 'Run failed: not_started' }),
    );
    expect(onError.mock.calls[0]?.[0].message).toBe(i18n.t('chatStream.runError.notStarted'));
  });
});

