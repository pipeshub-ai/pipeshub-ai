/**
 * U1 AR-2, CTL-4 end to end through the slot: an engine task's frames on the
 * chat stream reach the streaming answer's parts, and artifact cards on the
 * durable run stream reach the chat store, a card presented again replacing
 * itself. Only `fetch` is faked.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { installMemoryStorage, jwtExpiringIn, sseFrame } from '@/lib/api/__tests__/sse-response';
import type { StreamChatRequest } from '../types';
import { RUN_ID, SESSION_ID, finalConversation, toWire, type DurableFrame } from './durable-frames';

installMemoryStorage('localStorage');
installMemoryStorage('sessionStorage');

vi.mock('@/config', async () => {
  const auth = await vi.importActual<typeof import('@/lib/store/auth-store')>('@/lib/store/auth-store');
  return { useAuthStore: auth.useAuthStore, logoutAndRedirect: vi.fn() };
});

const { useAuthStore } = await import('@/config');
const { useFeatureFlagsStore } = await import('@/lib/store/feature-flags-store');
const { useChatStore } = await import('../store');
const { streamMessageForSlot, resumeActiveDurableRunForSlot } = await import('../streaming');

const fetchMock = vi.fn<typeof fetch>();
const initialState = useChatStore.getState();
const slot = (id: string) => useChatStore.getState().slots[id];

/** A response body the test feeds frame by frame, like a task in progress. */
function gatedBody(): { send: (text: string) => void; close: () => void; response: Response } {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c;
    },
  });
  return {
    send: (text) => controller.enqueue(new TextEncoder().encode(text)),
    close: () => controller.close(),
    response: new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } }),
  };
}

const frame = (type: string, fields: Record<string, unknown> = {}): string => sseFrame(type, { type, ...fields });
const progress = (kind: string, text: string, data: Record<string, unknown> = {}): string =>
  frame('CUSTOM', { name: 'engine_progress', value: { tool: 'code_task', taskId: 'ct-1', kind, text, data } });
const card = (lifecycle?: string) => ({
  artifactId: 'art-1',
  fileName: 'report.md',
  mimeType: 'text/markdown',
  sizeBytes: 22,
  downloadUrl: '',
  artifactType: 'DOCUMENT',
  recordId: 'art-1',
  ...(lifecycle ? { lifecycle } : {}),
});

const request: StreamChatRequest = {
  query: 'Write the report',
  modelKey: 'm1',
  modelName: 'gpt-5',
  modelFriendlyName: 'GPT-5',
  chatMode: 'quick',
  filters: { apps: [], kb: [] },
} as StreamChatRequest;

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
  useAuthStore.setState({ accessToken: jwtExpiringIn(3600) });
  useChatStore.setState({ ...initialState, slots: {}, activeSlotId: null, pendingConversations: {}, conversations: [] });
  useFeatureFlagsStore.setState({ agentHarness: null });
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  vi.spyOn(console, 'info').mockImplementation(() => {});
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('engine frames on the stream (CTL-4)', () => {
  it('reach the streaming answer as its engine part, and an artifact card presented twice stays one', async () => {
    const stream = gatedBody();
    fetchMock.mockResolvedValueOnce(stream.response);
    const slotId = useChatStore.getState().createSlot(SESSION_ID);
    useChatStore.setState({ activeSlotId: slotId });

    const run = streamMessageForSlot(slotId, request.query, { ...request, conversationId: SESSION_ID });
    stream.send(progress('started', 'Write the report') + progress('command', 'python report.py', { callId: 'c1' }));
    await vi.waitFor(() =>
      expect(slot(slotId).streamingParts.find((part) => part.type === 'engine_task')?.frames?.map((f) => f.kind)).toEqual([
        'started',
        'command',
      ]),
    );

    stream.send(
      frame('STATE_DELTA', { delta: [{ op: 'add', path: '/artifacts/-', value: card() }] }) +
        frame('STATE_DELTA', { delta: [{ op: 'add', path: '/artifacts/-', value: card() }] }) +
        progress('finished', 'Wrote report.md.', { success: true }),
    );
    await vi.waitFor(() => expect(slot(slotId).artifacts.map((a) => a.id)).toEqual(['art-1']));

    stream.send(frame('RUN_FINISHED', { result: { conversation: finalConversation('Done.') } }));
    stream.close();
    await run;
    expect(slot(slotId).isStreaming).toBe(false);
  });
});

describe('artifact cards on the durable run stream (AR-2)', () => {
  it('a draft card becomes final in place, and a discarded draft is removed', async () => {
    useFeatureFlagsStore.setState({
      agentHarness: { useHarness: true, harnessMajor: 2, reason: 'enabled', resumeInFlightRuns: true, harnessOn: true },
    });
    const stream = gatedBody();
    fetchMock.mockResolvedValueOnce(stream.response);
    const slotId = useChatStore.getState().createSlot(SESSION_ID);
    useChatStore.setState({ activeSlotId: slotId });
    useChatStore.getState().updateSlot(slotId, {
      messages: [{ role: 'user', content: [{ type: 'text', text: 'Question' }] }],
    });
    const frames = (specs: Array<[string, Record<string, unknown>]>, from: number): DurableFrame[] =>
      specs.map(([type, fields], i) => ({ id: `j:${from + i}.1/0.0.0`, event: type, data: { type, ...fields } }));
    const other = { ...card('draft'), artifactId: 'art-2', recordId: 'art-2', fileName: 'scratch.md' };

    const run = resumeActiveDurableRunForSlot(slotId, 'agent-1', SESSION_ID, RUN_ID);
    stream.send(
      toWire(
        frames(
          [
            ['RUN_STARTED', { threadId: SESSION_ID, runId: RUN_ID }],
            ['STATE_DELTA', { runId: SESSION_ID, delta: [{ op: 'add', path: '/artifacts/-', value: card('draft') }] }],
            ['STATE_DELTA', { runId: SESSION_ID, delta: [{ op: 'add', path: '/artifacts/-', value: other }] }],
          ],
          1,
        ),
      ),
    );
    await vi.waitFor(() => expect(slot(slotId).artifacts).toHaveLength(2));
    expect(slot(slotId).artifacts.map((a) => [a.id, a.lifecycle])).toEqual([
      ['art-1', 'draft'],
      ['art-2', 'draft'],
    ]);

    stream.send(
      toWire(
        frames(
          [
            ['STATE_DELTA', { runId: SESSION_ID, delta: [{ op: 'add', path: '/artifacts/-', value: card('final') }] }],
            ['STATE_DELTA', { runId: SESSION_ID, delta: [{ op: 'add', path: '/artifacts/-', value: { ...other, lifecycle: 'discarded' } }] }],
          ],
          4,
        ),
      ),
    );
    await vi.waitFor(() => expect(slot(slotId).artifacts).toHaveLength(1));
    expect(slot(slotId).artifacts.map((a) => [a.id, a.lifecycle])).toEqual([['art-1', undefined]]);

    stream.send(
      toWire(frames([['RUN_FINISHED', { result: { conversation: finalConversation('Done.'), meta: { requestId: RUN_ID } } }]], 6)),
    );
    stream.close();
    await run;
  });
});
