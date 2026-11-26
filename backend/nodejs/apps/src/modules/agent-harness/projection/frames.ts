import type { LiveChannel, LiveFrame } from '../ports/live-bus';
/**
 * The AG-UI wire vocabulary of the durable stream. `projection/` is the only
 * AG-UI code in the harness (REPORT 5.5); the frames are the ones the legacy
 * Python emitter and Node proxy produced (REPORT 8), plus an `id:` line.
 */

export const AGUI = {
  RUN_STARTED: 'RUN_STARTED',
  RUN_FINISHED: 'RUN_FINISHED',
  RUN_ERROR: 'RUN_ERROR',
  STATE_SNAPSHOT: 'STATE_SNAPSHOT',
  STATE_DELTA: 'STATE_DELTA',
  STEP_STARTED: 'STEP_STARTED',
  STEP_FINISHED: 'STEP_FINISHED',
  CUSTOM: 'CUSTOM',
  TEXT_MESSAGE_START: 'TEXT_MESSAGE_START',
  TEXT_MESSAGE_CONTENT: 'TEXT_MESSAGE_CONTENT',
  TEXT_MESSAGE_END: 'TEXT_MESSAGE_END',
  REASONING_START: 'REASONING_START',
  REASONING_MESSAGE_START: 'REASONING_MESSAGE_START',
  REASONING_MESSAGE_CONTENT: 'REASONING_MESSAGE_CONTENT',
  REASONING_MESSAGE_END: 'REASONING_MESSAGE_END',
  REASONING_END: 'REASONING_END',
  TOOL_CALL_START: 'TOOL_CALL_START',
  TOOL_CALL_ARGS: 'TOOL_CALL_ARGS',
  TOOL_CALL_END: 'TOOL_CALL_END',
  TOOL_CALL_RESULT: 'TOOL_CALL_RESULT',
} as const;

/**
 * The CUSTOM frame names the durable stream sends (`contracts/harness/vectors/ui-stream-events.json`, UX-01):
 * the web app renders or deliberately hides each one, so a name is added here and to the vector together.
 */
export const AGUI_CUSTOM = {
  CONVERSATION_CREATED: 'conversation_created',
  USER_INPUT: 'user_input',
  ASK_USER_QUESTION: 'ask_user_question',
  TOOL_UNAVAILABLE: 'tool_unavailable',
  APPROVAL_REQUESTED: 'approval_requested',
  APPROVAL_RESOLVED: 'approval_resolved',
  UNKNOWN_EFFECT: 'unknown_effect',
  UNKNOWN_EFFECT_RESOLVED: 'unknown_effect_resolved',
  CONNECT_REQUESTED: 'connect_requested',
  CONNECT_RESOLVED: 'connect_resolved',
  INTERACTION_WAITING: 'interaction_waiting',
  ENGINE_PROGRESS: 'engine_progress',
  PLAN: 'plan',
  NOTICE: 'notice',
  SLASH_COMMAND: 'slash_command',
  CONTEXT_COMPACTED: 'context_compacted',
  MESSAGE_DISCARDED: 'message_discarded',
  RETRIEVAL_CONTEXT: 'retrieval_context',
  RUN_USAGE: 'run_usage',
  /** WP-100: the run's spend from the ledger, pushed to an owner reading the outcome frames. */
  RUN_COST: 'run_cost',
  /** WP-100: one more approval counted on an ask that needs several; counts only, never who. */
  APPROVAL_COUNTED: 'approval_counted',
} as const;

export type AguiCustomName = (typeof AGUI_CUSTOM)[keyof typeof AGUI_CUSTOM];

export interface AguiFrame {
  type: string;
  [field: string]: unknown;
}

export interface ProjectedFrame {
  id: string;
  frame: AguiFrame;
}

/**
 * Who is watching (REPORT 8, S-F17): the run's owner sees everything; a
 * viewer of a shared conversation sees final answers, artifacts and
 * citations, never tool arguments, results, narration or reasoning. An
 * external surface is the owner reading through a channel, and gets exactly
 * what that channel's legacy route rendered (`audiences.ts`): an MCP client
 * the answer (`external`), the Slack bot its activity timeline and streamed
 * answer too (`slack`).
 */
export type Audience = 'owner' | 'shared' | 'external' | 'slack';

/** One LiveBus frame (`contracts/harness/vectors/livebus-frames.json`). */
export interface LiveDelta {
  runId: string;
  fence: number;
  modelAttempt: number;
  n: number;
  kind: 'text_delta' | 'reasoning_delta' | 'tool_call';
  /**
   * A `text_delta` may also carry the worker's numbering of the attempt's text
   * so far (`answer`) and, when they changed, its `citations` (CITESTREAM).
   */
  data: { text?: string; answer?: string; citations?: unknown[] } & Record<
    string,
    unknown
  >;
}

/** Hybrid AG-UI framing (`event:` carries the type, `data:` the full JSON), with its id. */
export const formatSseFrame = ({ id, frame }: ProjectedFrame): string =>
  `id: ${id}\nevent: ${frame.type}\ndata: ${JSON.stringify(frame)}\n\n`;

/** An SSE comment line: keeps proxies from closing an idle stream. */
export const SSE_HEARTBEAT = ': keep-alive\n\n';

/** The chat's live channel: token deltas only (UX5 keeps command output on its own channel). */
export const CHAT_LIVE_CHANNEL: LiveChannel<LiveDelta> = Object.freeze({
  name: 'live',
  kinds: new Set<LiveDelta['kind']>([
    'text_delta',
    'reasoning_delta',
    'tool_call',
  ]),
});

export const carries = (
  channel: LiveChannel<LiveFrame>,
  kind: string,
): boolean => channel.kinds.has(kind);
