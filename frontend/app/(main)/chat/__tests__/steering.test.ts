/**
 * P1b T2 in the web client (ST-8, ST-10): the running task takes a message
 * the user sends while it works; the stream shows it where the run took it,
 * the same place the saved conversation keeps it.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { apiClient } from '@/lib/api';
import { createAGUIEventHandler } from '../agui-event-handler';
import { ChatApi, type StreamMessageCallbacks } from '../api';
import { canSteerSlot, steerRunningTask } from '../steering';
import { useChatStore } from '../store';
import type { ChatSlot, MessagePart } from '../types';

vi.mock('@/lib/api', () => ({
  apiClient: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
  streamSSERequest: vi.fn(),
  streamSSEGet: vi.fn(),
}));

vi.mock('../runtime', () => ({
  ATTACHMENT_ONLY_STREAM_QUERY: 'See below attached file(s).',
  buildStreamChatRequestForSlot: (_slotId: string, query: string) => ({
    query,
    modelKey: 'model-1',
    modelName: 'Model',
    modelFriendlyName: 'Model',
    chatMode: 'agent:auto',
    filters: { apps: [], kb: [] },
    agentId: 'agent-1',
  }),
}));

const mockedPost = vi.mocked(apiClient.post);

/** Written by the Node projector's contract test from the golden steered journals. */
const JOURNALS = join(__dirname, '..', '..', '..', '..', '..', 'contracts', 'harness', 'vectors', 'journals');
const vector = <T>(file: string): T => JSON.parse(readFileSync(join(JOURNALS, file), 'utf8')) as T;

/** The client's final parts after folding the owner's frames of a steered run. */
function fold(scenario: string): { parts: MessagePart[]; answer: string } {
  const { frames } = vector<{ frames: Array<Record<string, unknown>> }>(`${scenario}.frames.json`);
  let parts: MessagePart[] = [];
  let answer = '';
  const callbacks: StreamMessageCallbacks = {
    onParts: (next) => {
      parts = JSON.parse(JSON.stringify(next)) as MessagePart[];
    },
    onChunk: (data) => {
      answer = data.accumulated;
    },
  };
  const handle = createAGUIEventHandler(callbacks, undefined, { replaceRestartedMessages: true });
  frames.forEach((frame, index) => handle({ id: `f${index}`, event: String(frame['type']), data: frame }));
  return { parts, answer };
}

/** What a reader sees: kinds in order, the steering text, which text is the answer. */
const shape = (parts: MessagePart[]) =>
  parts
    .filter((part) => part.type !== 'text' || (part.content ?? '') !== '')
    .map((part) =>
      part.type === 'user_input'
        ? ['user_input', part.content, part.messageSeq]
        : part.type === 'text'
          ? ['text', part.isFinal === true]
          : [part.type],
    );

describe('ST-8: the live timeline places the steering message where the saved one is', () => {
  for (const scenario of ['agent_steered_during_tool', 'agent_steered_after_answer']) {
    it(scenario, () => {
      const live = fold(scenario);
      const saved = vector<{ parts: MessagePart[] }>(`${scenario}.parts.json`).parts;
      expect(shape(live.parts)).toEqual(shape(saved));
      const final = saved.find((part) => part.isFinal === true);
      expect(live.answer).toBe(final?.content);
    });
  }

  it('what streamed before the message leaves the answer area when the run takes it', () => {
    const { frames } = vector<{ frames: Array<Record<string, unknown>> }>('agent_steered_after_answer.frames.json');
    let parts: MessagePart[] = [];
    let answer = '';
    const handle = createAGUIEventHandler(
      {
        onParts: (next) => (parts = JSON.parse(JSON.stringify(next)) as MessagePart[]),
        onChunk: (data) => (answer = data.accumulated),
      },
      undefined,
      { replaceRestartedMessages: true },
    );
    const at = frames.findIndex((frame) => frame['type'] === 'CUSTOM' && frame['name'] === 'user_input');
    frames.slice(0, at + 1).forEach((frame, index) => handle({ id: `f${index}`, event: String(frame['type']), data: frame }));
    // The first reply is narration now (shown in the timeline, above the message), not the answer.
    expect(answer).toBe('');
    expect(parts.map((part) => [part.type, part.settled === true])).toEqual([
      ['text', true],
      ['user_input', false],
    ]);
  });

  it('C-21b: the message shows the files it carried; malformed entries are dropped', () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    const file = { recordId: 'rec-1', virtualRecordId: 'v-1', recordName: 'q4.pdf', mimeType: 'application/pdf' };
    handle({
      id: 'a',
      event: 'CUSTOM',
      data: { type: 'CUSTOM', name: 'user_input', value: { messageSeq: 1, content: 'and this', attachments: [file, { recordName: 'x' }, 7] }, runId: 'r' },
    });
    expect(parts.find((part) => part.type === 'user_input')?.attachments).toEqual([
      { ...file, extension: '' },
    ]);
  });

  it('a replayed user_input frame is shown once', () => {
    let parts: MessagePart[] = [];
    const handle = createAGUIEventHandler({ onParts: (next) => (parts = [...next]) });
    const frame = { type: 'CUSTOM', name: 'user_input', value: { messageSeq: 1, content: 'hi' }, runId: 'r' };
    handle({ id: 'a', event: 'CUSTOM', data: frame });
    handle({ id: 'b', event: 'CUSTOM', data: frame });
    expect(parts.filter((part) => part.type === 'user_input')).toHaveLength(1);
  });
});

describe('ST-10: ChatApi.steerDurableRun', () => {
  const run = { agentId: 'agent/1', sessionId: 'session-1', runId: 'run-1' };
  const request = {
    query: 'Also the 2025 policy',
    modelKey: 'model-1',
    modelName: 'Model',
    modelFriendlyName: 'Model',
    chatMode: 'agent:auto',
    filters: { apps: [], kb: [] },
    agentId: 'agent/1',
    conversationId: 'session-1',
  };
  const refusal = (code: string) =>
    Object.assign(new Error('refused'), { originalError: { response: { data: { error: { code } } } } });

  beforeEach(() => mockedPost.mockReset());

  it('posts the turn to the running session with steer: true, and reads the queued message', async () => {
    mockedPost.mockResolvedValueOnce({
      data: { admission: { sessionId: 'session-1', run: { runId: 'run-1' }, queued: { messageSeq: 2 } } },
    });
    await expect(ChatApi.steerDurableRun(request, run, 'key-1')).resolves.toEqual({ status: 'queued', messageSeq: 2 });
    expect(mockedPost).toHaveBeenCalledWith(
      '/api/v1/agents/agent%2F1/conversations/session-1/runs',
      expect.objectContaining({ query: 'Also the 2025 policy', steer: true, idempotencyKey: 'key-1', chatMode: 'auto' }),
      { suppressErrorToast: true },
    );
  });

  it('SYNC4: the steered turn carries the selection below app level, as main\'s stream body does', async () => {
    mockedPost.mockResolvedValueOnce({ data: { admission: { queued: { messageSeq: 3 } } } });
    const selected = { ...request, filters: { apps: [], kb: [], recordGroups: ['group-1'], records: [], recordsExact: ['file-1'] } };
    await ChatApi.steerDurableRun(selected, run, 'key-2');
    expect(mockedPost.mock.calls[0][1].filters).toEqual({
      apps: [], kb: [], recordGroups: ['group-1'], recordsExact: ['file-1'],
    });
  });

  it('assistant chat posts to its own session route', async () => {
    mockedPost.mockResolvedValueOnce({ data: { admission: { queued: { messageSeq: 1 } } } });
    const { agentId: _agentId, ...chat } = { ...request, chatMode: 'internal_search' };
    await ChatApi.steerDurableRun(chat, { sessionId: 'chat-1', runId: 'run-2' });
    expect(mockedPost).toHaveBeenCalledWith(
      '/api/v1/conversations/chat-1/runs',
      expect.objectContaining({ steer: true, chatMode: 'internal_search' }),
      { suppressErrorToast: true },
    );
  });

  it('a task that ended meanwhile is not_active; any other refusal is refused', async () => {
    mockedPost.mockRejectedValueOnce(refusal('HTTP_AGENT_HARNESS_RUN_NOT_ACTIVE'));
    await expect(ChatApi.steerDurableRun(request, run)).resolves.toEqual({ status: 'not_active' });
    mockedPost.mockRejectedValueOnce(refusal('HTTP_AGENT_HARNESS_RUN_ACTIVE'));
    await expect(ChatApi.steerDurableRun(request, run)).resolves.toEqual({
      status: 'refused',
      code: 'HTTP_AGENT_HARNESS_RUN_ACTIVE',
    });
    mockedPost.mockResolvedValueOnce({ data: { admission: null } });
    await expect(ChatApi.steerDurableRun(request, run)).resolves.toEqual({ status: 'refused', code: null });
  });

  it('SS2: a message to someone else\'s running task waits as the sender\'s next run', async () => {
    mockedPost.mockResolvedValueOnce({
      data: {
        admission: {
          sessionId: 'session-1',
          run: { runId: 'run-1' },
          nextRun: { id: 'next-1', runId: 'run-9', position: 1 },
        },
      },
    });
    await expect(ChatApi.steerDurableRun(request, run)).resolves.toEqual({
      status: 'next_run',
      runId: 'run-9',
      position: 1,
    });
  });
});

describe('ST-10: only a durable run that is not being stopped takes a message', () => {
  const slot = (patch: Partial<ChatSlot>) => ({ isStreaming: true, stopping: false, durableRun: null, ...patch }) as ChatSlot;
  const durableRun = { sessionId: 's', runId: 'r' };

  it.each([
    ['a durable run streaming', slot({ durableRun }), true],
    ['a legacy stream (flag off)', slot({}), false],
    ['a durable run being stopped', slot({ durableRun, stopping: true }), false],
    ['an idle slot', slot({ durableRun, isStreaming: false }), false],
    ['no slot', undefined, false],
  ])('%s', (_name, value, expected) => {
    expect(canSteerSlot(value)).toBe(expected);
  });
});

describe('ST-10: steerRunningTask', () => {
  beforeEach(() => mockedPost.mockReset());

  it('sends to the slot\'s run; a task that ended meanwhile resolves once its stream closed', async () => {
    const store = useChatStore.getState();
    const slotId = store.createSlot('session-1');
    store.updateSlot(slotId, { isStreaming: true, durableRun: { agentId: 'agent-1', sessionId: 'session-1', runId: 'run-1' } });
    mockedPost.mockRejectedValueOnce(
      Object.assign(new Error('x'), { originalError: { response: { data: { error: { code: 'HTTP_AGENT_HARNESS_RUN_NOT_ACTIVE' } } } } }),
    );
    let settled = false;
    const outcome = steerRunningTask(slotId, 'next').then((value) => {
      settled = true;
      return value;
    });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(mockedPost).toHaveBeenCalledWith(
      '/api/v1/agents/agent-1/conversations/session-1/runs',
      expect.objectContaining({ query: 'next', steer: true }),
      { suppressErrorToast: true },
    );
    expect(settled, 'waits for the ended run\'s stream').toBe(false);
    useChatStore.getState().updateSlot(slotId, { isStreaming: false, durableRun: null });
    await expect(outcome).resolves.toEqual({ status: 'not_active' });
  });
});

describe('C-21b: steerRunningTask carries the files by reference', () => {
  beforeEach(() => mockedPost.mockReset());
  const file = { recordId: 'rec-1', virtualRecordId: 'v-1', recordName: 'q4.pdf', mimeType: 'application/pdf', extension: 'pdf' };

  const runningSlot = () => {
    const store = useChatStore.getState();
    const slotId = store.createSlot('session-2');
    store.updateSlot(slotId, { isStreaming: true, durableRun: { agentId: 'agent-1', sessionId: 'session-2', runId: 'run-2' } });
    return slotId;
  };

  it('with text', async () => {
    mockedPost.mockResolvedValueOnce({ data: { admission: { queued: { messageSeq: 3 } } } });
    await expect(steerRunningTask(runningSlot(), 'and this', [file])).resolves.toEqual({ status: 'queued', messageSeq: 3 });
    expect(mockedPost).toHaveBeenCalledWith(
      '/api/v1/agents/agent-1/conversations/session-2/runs',
      expect.objectContaining({ query: 'and this', steer: true, attachments: [file] }),
      { suppressErrorToast: true },
    );
  });

  it('alone, under the query a new turn with files alone sends', async () => {
    mockedPost.mockResolvedValueOnce({ data: { admission: { queued: { messageSeq: 1 } } } });
    await steerRunningTask(runningSlot(), '', [file]);
    expect(mockedPost).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ query: 'See below attached file(s).', attachments: [file] }),
      { suppressErrorToast: true },
    );
  });

  it('nothing to send is refused without a request', async () => {
    await expect(steerRunningTask(runningSlot(), '')).resolves.toEqual({ status: 'refused', code: null });
    expect(mockedPost).not.toHaveBeenCalled();
  });
});
