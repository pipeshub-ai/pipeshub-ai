/**
 * Durable run streams in the shape `GET …/runs/:runId/stream` sends them
 * (backend `agent-harness/projection/run-projector.ts`): the legacy AG-UI
 * vocabulary with an `id:` on every frame.
 */
import type { SSEEvent } from '@/lib/api';

export const RUN_ID = 'run-1';
export const SESSION_ID = 'session-1';

export interface DurableFrame extends SSEEvent {
  id: string;
}

export const ANSWER = 'The answer is 42.';

export function finalConversation(answer = ANSWER, status?: string): Record<string, unknown> {
  return {
    _id: SESSION_ID,
    title: 'Question',
    modelInfo: {},
    messages: [
      { _id: 'u1', messageType: 'user_query', content: 'Question', createdAt: '2026-09-26T00:00:00Z' },
      {
        _id: 'b1',
        messageType: 'bot_response',
        content: answer,
        createdAt: '2026-09-26T00:00:01Z',
        ...(status ? { status } : {}),
      },
    ],
  };
}

function build(specs: Array<[string, Record<string, unknown>]>): DurableFrame[] {
  return specs.map(([type, fields], index) => ({
    id: `j:${index + 1}.1/0.0.0`,
    event: type,
    data: { type, ...fields },
  }));
}

const start = (): Array<[string, Record<string, unknown>]> => [
  ['CUSTOM', { name: 'conversation_created', value: { conversationId: SESSION_ID, title: 'Question' } }],
  ['RUN_STARTED', { threadId: SESSION_ID, runId: RUN_ID }],
  ['STATE_SNAPSHOT', { runId: RUN_ID, snapshot: { status: 'starting' } }],
  ['STATE_SNAPSHOT', { runId: RUN_ID, snapshot: { status: 'calling_llm' } }],
  ['TEXT_MESSAGE_START', { messageId: 'msg_a', role: 'assistant', runId: RUN_ID }],
  ['TEXT_MESSAGE_CONTENT', { messageId: 'msg_a', delta: 'Let me ', runId: RUN_ID }],
  ['TEXT_MESSAGE_CONTENT', { messageId: 'msg_a', delta: 'search.', runId: RUN_ID }],
  ['TEXT_MESSAGE_END', { messageId: 'msg_a', runId: RUN_ID }],
  ['TOOL_CALL_START', { toolCallId: 'tc1', toolCallName: 'search', runId: RUN_ID }],
  ['TOOL_CALL_ARGS', { toolCallId: 'tc1', delta: '{"q":"x"}', runId: RUN_ID }],
  ['STATE_SNAPSHOT', { runId: RUN_ID, snapshot: { status: 'running_tool', current_tool: 'search' } }],
  ['TOOL_CALL_END', { toolCallId: 'tc1', runId: RUN_ID }],
  ['TOOL_CALL_RESULT', { toolCallId: 'tc1', content: 'found', role: 'tool', status: 'success', runId: RUN_ID }],
  ['STATE_SNAPSHOT', { runId: RUN_ID, snapshot: { status: 'calling_llm' } }],
  ['TEXT_MESSAGE_START', { messageId: 'msg_b', role: 'assistant', runId: RUN_ID }],
  ['TEXT_MESSAGE_CONTENT', { messageId: 'msg_b', delta: 'The answer', runId: RUN_ID }],
  ['TEXT_MESSAGE_CONTENT', { messageId: 'msg_b', delta: ' is 4', runId: RUN_ID }],
];

const finish = (answer: string): Array<[string, Record<string, unknown>]> => [
  [
    'STATE_DELTA',
    {
      runId: RUN_ID,
      delta: [
        { op: 'replace', path: '/citations', value: [] },
        { op: 'replace', path: '/normalizedAnswer', value: answer },
        { op: 'replace', path: '/rawLength', value: answer.length },
      ],
    },
  ],
  ['STATE_SNAPSHOT', { runId: RUN_ID, snapshot: { status: 'completed' } }],
  ['STATE_SNAPSHOT', { runId: RUN_ID, snapshot: { final: true } }],
  [
    'RUN_FINISHED',
    { result: { conversation: finalConversation(answer), meta: { requestId: RUN_ID, duration: 1 } } },
  ],
];

/** A run with a narrated tool call and a streamed final answer. */
export function completedRun(): DurableFrame[] {
  return build([
    ...start(),
    ['TEXT_MESSAGE_CONTENT', { messageId: 'msg_b', delta: '2.', runId: RUN_ID }],
    ['TEXT_MESSAGE_END', { messageId: 'msg_b', runId: RUN_ID }],
    ...finish(ANSWER),
  ]);
}

/**
 * The same run whose answer message was abandoned mid-stream (lost buffer or
 * re-claim, L-2/L-10) and restarted whole under a new id.
 */
export function restartedRun(restartedText = ANSWER): DurableFrame[] {
  return build([
    ...start(),
    ['TEXT_MESSAGE_END', { messageId: 'msg_b', runId: RUN_ID }],
    ['TEXT_MESSAGE_START', { messageId: 'msg_b_restart', role: 'assistant', runId: RUN_ID }],
    ['TEXT_MESSAGE_CONTENT', { messageId: 'msg_b_restart', delta: restartedText, runId: RUN_ID }],
    ['TEXT_MESSAGE_END', { messageId: 'msg_b_restart', runId: RUN_ID }],
    ...finish(restartedText),
  ]);
}

/**
 * The same restart, marked by the projector (`replacesMessageId`, PA-11) and
 * not adjacent to the end of the replaced message: another frame between
 * them (`gap`), or no end at all (`no_end`).
 */
export function markedRestartRun(shape: 'adjacent' | 'gap' | 'no_end'): DurableFrame[] {
  return build([
    ...start(),
    ...(shape === 'no_end'
      ? []
      : ([['TEXT_MESSAGE_END', { messageId: 'msg_b', runId: RUN_ID }]] as Array<[string, Record<string, unknown>]>)),
    ...(shape === 'gap'
      ? ([['STATE_SNAPSHOT', { runId: RUN_ID, snapshot: { status: 'calling_llm' } }]] as Array<[string, Record<string, unknown>]>)
      : []),
    [
      'TEXT_MESSAGE_START',
      { messageId: 'msg_b_restart', role: 'assistant', runId: RUN_ID, replacesMessageId: 'msg_b' },
    ],
    ['TEXT_MESSAGE_CONTENT', { messageId: 'msg_b_restart', delta: ANSWER, runId: RUN_ID }],
    ['TEXT_MESSAGE_END', { messageId: 'msg_b_restart', runId: RUN_ID }],
    ...finish(ANSWER),
  ]);
}

/** A cancelled run: the stopped partial answer comes back in RUN_FINISHED. */
export function cancelledRun(partial = 'The answer is 4'): DurableFrame[] {
  const frames = build([
    ...start(),
    ['TEXT_MESSAGE_END', { messageId: 'msg_b', runId: RUN_ID }],
    ['STATE_SNAPSHOT', { runId: RUN_ID, snapshot: { status: 'cancelled' } }],
    ['STATE_SNAPSHOT', { runId: RUN_ID, snapshot: { final: true, status: 'stopped' } }],
    [
      'RUN_FINISHED',
      {
        result: {
          conversation: finalConversation(partial, 'stopped'),
          meta: { requestId: RUN_ID, duration: 1 },
        },
      },
    ],
  ]);
  return frames;
}

export function runError(code = 'model_unavailable'): DurableFrame[] {
  return build([
    ...start().slice(0, 4),
    ['RUN_ERROR', { message: 'The model is not available right now.', code }],
  ]);
}

export function toWire(frames: DurableFrame[]): string {
  return frames
    .map((frame) => `id: ${frame.id}\nevent: ${frame.event}\ndata: ${JSON.stringify(frame.data)}\n\n`)
    .join('');
}

/** The run as a chat route streams it to its owner (LAT1): conversation_created names the run. */
export function asOwner(frames: DurableFrame[]): DurableFrame[] {
  return frames.map((frame) => {
    const data = frame.data as { name?: unknown; value?: Record<string, unknown> };
    return data.name === 'conversation_created'
      ? { ...frame, data: { ...frame.data as object, value: { ...data.value, runId: RUN_ID } } }
      : frame;
  });
}
