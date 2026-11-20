import { LABEL_ROW_FIELDS } from '../services/conversation/privacy/label.types';
import {
  flushAnswerDelta,
  sanitizeAnswerDelta,
  sanitizePartialAnswerMarkdown,
  type AnswerDeltaState,
} from './answer-sanitizer';

/**
 * What of the AI backend's stream reaches the browser. An event type, a custom event name or a field that is not
 * named here is dropped, so a field or event the AI backend adds later (a run's label among them) stays on the
 * server until it is named.
 */

/** Fields every forwarded event may carry. */
const BASE = ['type', 'runId', 'parentRunId', 'threadId'];

const FIELDS: Record<string, readonly string[]> = {
  RUN_STARTED: [],
  // Only a nested run's end is forwarded; the root run's carries the answer, which Node saves and sends itself.
  RUN_FINISHED: [],
  RUN_ERROR: ['message', 'code'],
  STEP_STARTED: ['stepName'],
  STEP_FINISHED: ['stepName'],
  TEXT_MESSAGE_START: ['messageId', 'role'],
  TEXT_MESSAGE_CONTENT: ['messageId', 'delta'],
  TEXT_MESSAGE_END: ['messageId'],
  REASONING_START: ['messageId'],
  REASONING_MESSAGE_START: ['messageId', 'role'],
  REASONING_MESSAGE_CONTENT: ['messageId', 'delta'],
  REASONING_MESSAGE_END: ['messageId'],
  REASONING_END: ['messageId'],
  TOOL_CALL_START: [
    'toolCallId',
    'toolCallName',
    'parentMessageId',
    'displayName',
    'approved',
    'approvalAnswer',
    'approvalReason',
  ],
  TOOL_CALL_ARGS: ['toolCallId', 'delta', 'argsSummary', 'argsPreview'],
  TOOL_CALL_END: ['toolCallId'],
  TOOL_CALL_RESULT: [
    'toolCallId',
    'messageId',
    'content',
    'role',
    'status',
    'resultSummary',
    'approval',
    'resultView',
  ],
  STATE_DELTA: ['delta'],
  STATE_SNAPSHOT: ['snapshot'],
  CUSTOM: ['name', 'value'],
  HEARTBEAT: [],
};

/** Each forwarded event type, with the fields it may carry. */
export const RELAYED_EVENT_FIELDS: ReadonlyMap<string, ReadonlySet<string>> =
  new Map(
    Object.entries(FIELDS).map(([type, fields]) => [
      type,
      new Set([...BASE, ...fields]),
    ]),
  );

/** The custom events the asker's client acts on. */
export const RELAYED_CUSTOM_EVENTS: ReadonlySet<string> = new Set([
  'ask_user_question',
  'message_discarded',
  'tool_unavailable',
  'tools_unavailable',
  'artifact',
  // Asked for by an evaluation client, for its own run.
  'retrieval_context',
  'run_usage',
]);

/** The progress the client shows of a running tool; the final snapshot sends none of the answer data it carries. */
const PROGRESS_FIELDS: ReadonlySet<string> = new Set([
  'status',
  'current_tool',
  'progress',
  'total',
  'progress_message',
  'run_id',
  'parent_run_id',
  'role_name',
  'current_turn',
]);

/** The answer-state paths the client reads: the numbered copy of the answer, its citations, and artifacts. */
const STATE_PATHS: ReadonlySet<string> = new Set([
  '/normalizedAnswer',
  '/rawLength',
  '/citations',
  '/confidence',
  '/discarded',
  '/artifacts/-',
]);

const LABEL_FIELDS: ReadonlySet<string> = new Set(LABEL_ROW_FIELDS);

/** Deeper than any value an event carries today: a subtree past it is dropped, not sent unread. */
const MAX_DEPTH = 32;

type Frame = Record<string, unknown>;

const isPlain = (value: unknown): value is Frame =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

const frameOf = (type: string, frame: Frame): string =>
  `event: ${type}\ndata: ${JSON.stringify(frame)}\n\n`;

/** `value` without any label held under one of the label fields' names; a plain value of the same name is kept. */
function withoutNestedLabels(value: unknown, depth: number): unknown {
  if (value === null || typeof value !== 'object') return value;
  if (depth >= MAX_DEPTH) return null;
  if (Array.isArray(value)) {
    return value.map((item) => withoutNestedLabels(item, depth + 1));
  }
  const out: Frame = {};
  for (const [key, item] of Object.entries(value as Frame)) {
    if (LABEL_FIELDS.has(key) && item !== null && typeof item === 'object') {
      continue;
    }
    out[key] = withoutNestedLabels(item, depth + 1);
  }
  return out;
}

function snapshotFrame(frame: Frame): Frame {
  const { snapshot, ...rest } = frame;
  if (!isPlain(snapshot)) return rest;
  if (snapshot.final === true) return { ...rest, snapshot: { final: true } };
  return {
    ...rest,
    snapshot: Object.fromEntries(
      Object.entries(snapshot).filter(([key]) => PROGRESS_FIELDS.has(key)),
    ),
  };
}

const CONTENT_OF: Record<string, string> = {
  TEXT_MESSAGE_END: 'TEXT_MESSAGE_CONTENT',
  REASONING_MESSAGE_END: 'REASONING_MESSAGE_CONTENT',
};

/**
 * One relay per stream: it keeps the answer sanitiser's state for each text message the stream writes.
 * `keptAsCard` names the custom events the edition stores as cards; the asker's client draws those as they come.
 */
export class StreamRelay {
  private readonly deltas = new Map<string, AnswerDeltaState>();

  constructor(
    private readonly keptAsCard: (eventName: string) => boolean = () => false,
  ) {}

  /** The frames the browser gets for one AI-backend frame: '' when none. */
  relay(eventType: string | undefined, data: string): string {
    if (eventType === undefined || data === '') return '';
    const fields = RELAYED_EVENT_FIELDS.get(eventType);
    if (!fields) return '';
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch {
      return '';
    }
    if (!isPlain(parsed)) return '';
    const frame: Frame = { type: eventType };
    for (const [key, value] of Object.entries(parsed)) {
      if (key !== 'type' && fields.has(key)) frame[key] = value;
    }
    switch (eventType) {
      case 'CUSTOM':
        return this.custom(frame);
      case 'STATE_SNAPSHOT':
        return frameOf(eventType, snapshotFrame(frame));
      case 'STATE_DELTA':
        return this.stateDelta(frame);
      case 'TEXT_MESSAGE_CONTENT':
      case 'REASONING_MESSAGE_CONTENT':
        return this.textDelta(eventType, frame);
      case 'TEXT_MESSAGE_END':
      case 'REASONING_MESSAGE_END':
        return this.textEnd(eventType, frame);
      default:
        return frameOf(eventType, frame);
    }
  }

  private custom(frame: Frame): string {
    const { name } = frame;
    if (
      typeof name !== 'string' ||
      !(RELAYED_CUSTOM_EVENTS.has(name) || this.keptAsCard(name))
    ) {
      return '';
    }
    if (name === 'message_discarded') {
      const messageId = (frame.value as { messageId?: unknown } | undefined)
        ?.messageId;
      this.deltas.delete(
        this.keyOf('TEXT_MESSAGE_CONTENT', { ...frame, messageId }),
      );
    }
    if ('value' in frame) frame.value = withoutNestedLabels(frame.value, 0);
    return frameOf('CUSTOM', frame);
  }

  private stateDelta(frame: Frame): string {
    if (!Array.isArray(frame.delta)) return '';
    const ops: Frame[] = [];
    for (const op of frame.delta as unknown[]) {
      if (
        !isPlain(op) ||
        typeof op.op !== 'string' ||
        typeof op.path !== 'string' ||
        !STATE_PATHS.has(op.path)
      ) {
        continue;
      }
      let value: unknown;
      if (op.path === '/normalizedAnswer' && typeof op.value === 'string') {
        try {
          // The whole answer so far, on every chunk: a partial text, never asked for again, so nothing of it is kept.
          value = sanitizePartialAnswerMarkdown(op.value);
        } catch {
          continue;
        }
      } else {
        value = withoutNestedLabels(op.value, 0);
      }
      ops.push({ op: op.op, path: op.path, ...('value' in op && { value }) });
    }
    return ops.length > 0 ? frameOf('STATE_DELTA', { ...frame, delta: ops }) : '';
  }

  private textDelta(type: string, frame: Frame): string {
    if (typeof frame.delta !== 'string') return '';
    const key = this.keyOf(type, frame);
    let next: { state: AnswerDeltaState; out: string };
    try {
      next = sanitizeAnswerDelta(this.deltas.get(key), frame.delta);
    } catch {
      return '';
    }
    this.deltas.set(key, next.state);
    return frameOf(type, { ...frame, delta: next.out });
  }

  /** A message's end, after whatever the sanitiser still held back of it. */
  private textEnd(type: string, frame: Frame): string {
    const contentType = CONTENT_OF[type] as string;
    const key = this.keyOf(contentType, frame);
    const state = this.deltas.get(key);
    this.deltas.delete(key);
    let held = '';
    if (state !== undefined) {
      try {
        held = flushAnswerDelta(state).out;
      } catch {
        held = '';
      }
    }
    const { type: _type, ...ids } = frame;
    const flush =
      held === ''
        ? ''
        : frameOf(contentType, { type: contentType, ...ids, delta: held });
    return flush + frameOf(type, frame);
  }

  private keyOf(contentType: string, frame: Frame): string {
    return `${contentType}:${String(frame.runId ?? '')}:${String(frame.messageId ?? '')}`;
  }
}
