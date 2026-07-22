/**
 * AG-UI wire protocol -> `StreamMessageCallbacks` adapter (AG-UI migration
 * plan, Phase 3a/3b). `streaming.ts`'s slot-scoped Zustand logic and
 * `frontend/lib/api/streaming.ts`'s `parseSSEBuffer` are both untouched —
 * this is the only place that understands AG-UI event shapes; every other
 * frontend module keeps talking to the same `StreamMessageCallbacks`
 * surface it always has.
 *
 * One handler instance per stream: create a fresh one per
 * `ChatApi.stream*` call (it closes over per-run text/citation buffers).
 */
import type { SSEEvent } from '@/lib/api';
import type {
  AGUIEventEnvelope,
  AGUIJsonPatchOp,
  MessagePart,
  SSEAskUserQuestionEvent,
  SSEArtifactEvent,
  SSEChunkCitation,
  SSEConnectedEvent,
} from './types';
import type { StreamMessageCallbacks } from './api';

/** Mutable counters the caller inspects after the stream ends (mirrors the
 * legacy dispatcher's local `receivedComplete`/`lastSSEError` bookkeeping). */
export interface AGUIStreamTracking {
  receivedComplete: boolean;
}

interface AGUIStreamState {
  citations: SSEChunkCitation[];
  normalizedAnswer: string;
}

// Mirrors the server-side caps in `transcript_collector.py` — defensive
// only, since the server already truncates before these deltas are sent.
const MAX_TOOL_ARGS_CHARS = 2000;
const MAX_TOOL_RESULT_CHARS = 500;

/**
 * Builds the live `MessagePart[]` timeline from the SAME AG-UI event stream
 * this handler already reads, mirroring the server-side `TranscriptCollector`
 * (`protocol/transcript_collector.py`) part-for-part so a reload (which
 * renders `ConversationMessage.parts`, built by that same collector) shows
 * an identical structure to what streamed live.
 *
 * One instance per stream (closed over by `createAGUIEventHandler`, never
 * exported) — keyed bookkeeping (`_containers`/`_openTurnParts`/
 * `_openToolCalls`) is scoped to a single run tree, same lifetime as the
 * `TranscriptCollector` it mirrors.
 */
class LivePartsBuilder {
  readonly parts: MessagePart[] = [];

  private containers = new Map<string, MessagePart[]>();

  private openTurnParts = new Map<string, MessagePart>();

  private openToolCalls = new Map<string, MessagePart>();

  /** Defense-in-depth against duplicate event processing (SSE retry, proxy
   * edge cases) — keyed on `messageId` for text starts and `toolCallId` for
   * tool call starts. */
  private seenIds = new Set<string>();

  /** Tracks live sub-agent parts by their runId so `handleRunEnded` can flip
   * `status` from `'running'` to `'completed'`/`'failed'`. */
  private subAgentParts = new Map<string, MessagePart>();

  /** `STEP_STARTED{stepName:"sub_agent:<role>"}` always precedes the child's
   * own `RUN_STARTED` (see `AGUIEventEmitter._translate`) — stash the role
   * name here, keyed by the parent's runId, until that `RUN_STARTED` lands. */
  private pendingRoleByParent = new Map<string, string>();

  private containerFor(runId: string | undefined): MessagePart[] {
    if (!runId) return this.parts;
    return this.containers.get(runId) ?? this.parts;
  }

  /** `true` when `runId` is the root run (or unset — most non-nested
   * events never carry a `runId` at all, see the existing TOOL_CALL_START
   * test fixtures) rather than a sub-agent's own run. Mirrors the
   * `run_context.parent_run_id is None` check `TranscriptCollector`/
   * `AGUIEventEmitter` use server-side. */
  isRootRun(runId: string | undefined): boolean {
    if (!runId) return true;
    const container = this.containers.get(runId);
    return container === undefined || container === this.parts;
  }

  /** Mirrors `TranscriptCollector.replace_final_text`'s `isFinal` marking
   * (see that method's docstring) — called on the root `RUN_FINISHED` so
   * the live timeline matches the persisted transcript's shape exactly:
   * every other root `text` part is narration, this one is the answer. */
  markLastRootTextFinal(): void {
    for (let i = this.parts.length - 1; i >= 0; i -= 1) {
      if (this.parts[i].type === 'text') {
        this.parts[i] = { ...this.parts[i], isFinal: true };
        return;
      }
    }
  }

  handleStepStarted(data: AGUIEventEnvelope): void {
    const stepName = typeof data.stepName === 'string' ? data.stepName : '';
    const parentRunId = typeof data.runId === 'string' ? data.runId : undefined;
    if (stepName.startsWith('sub_agent:') && parentRunId) {
      this.pendingRoleByParent.set(parentRunId, stepName.slice('sub_agent:'.length));
    }
  }

  handleRunStarted(data: AGUIEventEnvelope): void {
    const runId = typeof data.runId === 'string' ? data.runId : undefined;
    const parentRunId = typeof data.parentRunId === 'string' ? data.parentRunId : undefined;
    if (!runId) return;
    if (!parentRunId) {
      this.containers.set(runId, this.parts);
      return;
    }
    const roleName = this.pendingRoleByParent.get(parentRunId) ?? 'sub-agent';
    this.pendingRoleByParent.delete(parentRunId);
    const subAgentPart: MessagePart = {
      type: 'sub_agent', runId, roleName, parts: [], status: 'running',
    };
    this.containerFor(parentRunId).push(subAgentPart);
    this.containers.set(runId, subAgentPart.parts as MessagePart[]);
    this.subAgentParts.set(runId, subAgentPart);
  }

  handleRunEnded(
    data: AGUIEventEnvelope,
    status: 'completed' | 'failed' = 'completed',
  ): void {
    const runId = typeof data.runId === 'string' ? data.runId : undefined;
    if (!runId) return;
    const part = this.subAgentParts.get(runId);
    if (part) part.status = status;
    this.subAgentParts.delete(runId);
    this.containers.delete(runId);
  }

  handleToolCallStart(data: AGUIEventEnvelope): void {
    const toolCallId = typeof data.toolCallId === 'string' ? data.toolCallId : undefined;
    if (!toolCallId) return;
    if (this.seenIds.has(toolCallId)) return;
    this.seenIds.add(toolCallId);
    const runId = typeof data.runId === 'string' ? data.runId : undefined;
    const part: MessagePart = {
      type: 'tool_call',
      toolCallId,
      toolName: typeof data.toolCallName === 'string' ? data.toolCallName : 'tool',
      status: 'running',
      runId,
    };
    this.containerFor(runId).push(part);
    this.openToolCalls.set(toolCallId, part);
  }

  handleToolCallArgs(data: AGUIEventEnvelope): void {
    const toolCallId = typeof data.toolCallId === 'string' ? data.toolCallId : undefined;
    if (!toolCallId) return;
    const part = this.openToolCalls.get(toolCallId);
    if (!part) return;
    const delta = typeof data.delta === 'string' ? data.delta : '';
    part.args = delta.slice(0, MAX_TOOL_ARGS_CHARS);
    if (typeof data.argsSummary === 'string') {
      part.argsSummary = data.argsSummary;
    }
  }

  handleToolCallResult(data: AGUIEventEnvelope): void {
    const toolCallId = typeof data.toolCallId === 'string' ? data.toolCallId : undefined;
    if (!toolCallId) return;
    const part = this.openToolCalls.get(toolCallId);
    this.openToolCalls.delete(toolCallId);
    if (!part) return;
    const status = typeof data.status === 'string' ? data.status : 'completed';
    part.status = status as MessagePart['status'];
    part.resultPreview = String(data.content ?? '').slice(0, MAX_TOOL_RESULT_CHARS);
    if (typeof data.resultSummary === 'string') {
      part.resultSummary = data.resultSummary;
    }
  }

  handleTextStart(data: AGUIEventEnvelope): void {
    const messageId = typeof data.messageId === 'string' ? data.messageId : undefined;
    if (messageId) {
      if (this.seenIds.has(messageId)) return;
      this.seenIds.add(messageId);
    }
    const runId = typeof data.runId === 'string' ? data.runId : undefined;
    const part: MessagePart = { type: 'text', content: '', runId };
    this.containerFor(runId).push(part);
    this.openTurnParts.set(`${runId ?? ''}:text`, part);
  }

  handleTextContent(data: AGUIEventEnvelope): void {
    const runId = typeof data.runId === 'string' ? data.runId : undefined;
    const part = this.openTurnParts.get(`${runId ?? ''}:text`);
    if (!part) return;
    part.content = (part.content ?? '') + (typeof data.delta === 'string' ? data.delta : '');
  }

  handleTextEnd(data: AGUIEventEnvelope): void {
    const runId = typeof data.runId === 'string' ? data.runId : undefined;
    this.openTurnParts.delete(`${runId ?? ''}:text`);
  }

  handleReasoningStart(data: AGUIEventEnvelope): void {
    const runId = typeof data.runId === 'string' ? data.runId : undefined;
    const part: MessagePart = { type: 'reasoning', content: '', runId };
    this.containerFor(runId).push(part);
    this.openTurnParts.set(`${runId ?? ''}:reasoning`, part);
  }

  handleReasoningContent(data: AGUIEventEnvelope): void {
    const runId = typeof data.runId === 'string' ? data.runId : undefined;
    const part = this.openTurnParts.get(`${runId ?? ''}:reasoning`);
    if (!part) return;
    part.content = (part.content ?? '') + (typeof data.delta === 'string' ? data.delta : '');
  }

  handleReasoningEnd(data: AGUIEventEnvelope): void {
    const runId = typeof data.runId === 'string' ? data.runId : undefined;
    this.openTurnParts.delete(`${runId ?? ''}:reasoning`);
  }

  /** New top-level array reference each call so consumers (React state
   * setters) always see a change — nested part objects/arrays are mutated
   * in place, same trade-off `TranscriptCollector` makes server-side. */
  snapshot(): MessagePart[] {
    return this.parts.slice();
  }
}

/**
 * Applies the top-level `replace`/`add` ops AG-UI's `STATE_DELTA` carries
 * (see `AGUIFormatter.answer_delta` server-side) onto `{citations,
 * normalizedAnswer}`. Not a general JSON Patch implementation — PipesHub's
 * server only ever patches these two single-level paths.
 */
function applyStatePatch(state: AGUIStreamState, ops: AGUIJsonPatchOp[]): AGUIStreamState {
  let next = state;
  for (const op of ops) {
    if (op.op !== 'replace' && op.op !== 'add') continue;
    if (op.path === '/citations' && Array.isArray(op.value)) {
      next = { ...next, citations: op.value as SSEChunkCitation[] };
    } else if (op.path === '/normalizedAnswer' && typeof op.value === 'string') {
      next = { ...next, normalizedAnswer: op.value };
    }
  }
  return next;
}

/**
 * Builds an `onEvent` callback for `streamSSERequest` that translates AG-UI
 * frames into the existing `StreamMessageCallbacks`. Pass `tracking` so the
 * caller can detect "stream ended without RUN_FINISHED" the same way the
 * legacy dispatcher detects "stream ended without complete".
 */
export function createAGUIEventHandler(
  callbacks: StreamMessageCallbacks,
  tracking?: AGUIStreamTracking,
): (event: SSEEvent) => void {
  let textBuffer = '';
  let state: AGUIStreamState = { citations: [], normalizedAnswer: '' };
  const partsBuilder = new LivePartsBuilder();
  const emitParts = () => callbacks.onParts?.(partsBuilder.snapshot());

  return (event: SSEEvent) => {
    const data = event.data as AGUIEventEnvelope | undefined;
    const type = data?.type ?? event.event;

    switch (type) {
      case 'STEP_STARTED': {
        const stepName = typeof data?.stepName === 'string' ? data.stepName : '';
        if (stepName.startsWith('sub_agent:')) {
          const roleName = stepName.slice('sub_agent:'.length);
          callbacks.onStatus?.({ status: 'executing', message: `Delegating to ${roleName}...` });
        }
        if (data) partsBuilder.handleStepStarted(data);
        break;
      }

      case 'RUN_STARTED':
        if (data) {
          partsBuilder.handleRunStarted(data);
          if (data.parentRunId) emitParts();
        }
        break;

      case 'TEXT_MESSAGE_START': {
        // New model turn abandons any unfinished preamble — matches
        // TerminalAnswerStreamer's own per-turn buffer reset (plan 1e).
        const runId = typeof data?.runId === 'string' ? data.runId : undefined;
        if (partsBuilder.isRootRun(runId)) {
          textBuffer = '';
        }
        if (data) partsBuilder.handleTextStart(data);
        emitParts();
        break;
      }

      case 'TEXT_MESSAGE_CONTENT': {
        const delta = typeof data?.delta === 'string' ? data.delta : '';
        if (!delta) break;
        const runId = typeof data?.runId === 'string' ? data.runId : undefined;
        if (partsBuilder.isRootRun(runId)) {
          textBuffer += delta;
          callbacks.onChunk?.({ chunk: delta, accumulated: textBuffer, citations: state.citations });
        }
        if (data) partsBuilder.handleTextContent(data);
        emitParts();
        break;
      }

      case 'TEXT_MESSAGE_END':
        if (data) partsBuilder.handleTextEnd(data);
        break;

      case 'REASONING_MESSAGE_START':
        if (data) partsBuilder.handleReasoningStart(data);
        emitParts();
        break;

      case 'REASONING_MESSAGE_CONTENT': {
        const delta = typeof data?.delta === 'string' ? data.delta : '';
        const runId = typeof data?.runId === 'string' ? data.runId : undefined;
        if (delta && partsBuilder.isRootRun(runId)) {
          callbacks.onReasoning?.({ delta, done: false });
        }
        if (data) partsBuilder.handleReasoningContent(data);
        emitParts();
        break;
      }

      case 'REASONING_MESSAGE_END':
        if (data) partsBuilder.handleReasoningEnd(data);
        break;

      case 'REASONING_END':
        callbacks.onReasoning?.({ delta: '', done: true });
        break;

      case 'TOOL_CALL_START': {
        const toolCallName = typeof data?.toolCallName === 'string' ? data.toolCallName : 'tool';
        callbacks.onStatus?.({ status: 'executing', message: `Using ${toolCallName}...` });
        // A tool call is about to run for the turn that was just streamed —
        // it was never the final answer, so settle it into the timeline
        // (already appended as a `text` part above) and stop showing it in
        // the live answer buffer. Mirrors `TerminalAnswerStreamer.
        // _clear_preamble` server-side; no-ops on the 2nd+ tool call in the
        // same turn (buffer already cleared) and on child tool calls
        // (their own agent's answer buffer is untouched by a delegate).
        const runId = typeof data?.runId === 'string' ? data.runId : undefined;
        if (textBuffer && partsBuilder.isRootRun(runId)) {
          textBuffer = '';
          callbacks.onChunk?.({ chunk: '', accumulated: '', citations: state.citations });
        }
        if (data) partsBuilder.handleToolCallStart(data);
        emitParts();
        break;
      }

      case 'TOOL_CALL_ARGS':
        if (data) partsBuilder.handleToolCallArgs(data);
        emitParts();
        break;

      case 'TOOL_CALL_RESULT':
        if (data) partsBuilder.handleToolCallResult(data);
        emitParts();
        break;

      case 'STATE_DELTA': {
        const ops = Array.isArray(data?.delta) ? (data.delta as AGUIJsonPatchOp[]) : [];
        // `AGUIFormatter.artifact()` appends via `add /artifacts/-` on this
        // same event name (see `QueueEventSink._coalesce_key`'s coalescing
        // carve-out server-side) — route those to `onArtifact` and keep them
        // out of `applyStatePatch`/the text-buffer logic below, which only
        // understands the answer-delta `replace` ops.
        const textOps: AGUIJsonPatchOp[] = [];
        for (const op of ops) {
          if (op.op === 'add' && op.path === '/artifacts/-') {
            callbacks.onArtifact?.(op.value as SSEArtifactEvent);
          } else {
            textOps.push(op);
          }
        }
        if (textOps.length === 0) break;
        state = applyStatePatch(state, textOps);
        // Only adopt the corrected buffer once it has caught up with what's
        // already on screen — otherwise a lagging normalizedAnswer would
        // make the visible text momentarily shrink (see plan 1e caveat).
        if (state.normalizedAnswer.length >= textBuffer.length) {
          textBuffer = state.normalizedAnswer;
        }
        callbacks.onChunk?.({ chunk: '', accumulated: textBuffer, citations: state.citations });
        break;
      }

      case 'CUSTOM': {
        const name = data?.name;
        const value = data?.value;
        if (name === 'conversation_created') {
          callbacks.onConnected?.((value ?? {}) as SSEConnectedEvent);
        } else if (name === 'ask_user_question') {
          callbacks.onAskUserQuestion?.(value as SSEAskUserQuestionEvent);
        } else if (name === 'artifact') {
          callbacks.onArtifact?.(value as SSEArtifactEvent);
        }
        break;
      }

      case 'RUN_FINISHED': {
        // A child (sub-agent) RUN_FINISHED carries no `result` — only the
        // root run's (re-emitted by Node with `{conversation, meta}`) does
        // — but it DOES close that child's parts container, same as
        // `TranscriptCollector._collect`'s RUN_FINISHED branch.
        if (data?.parentRunId) {
          partsBuilder.handleRunEnded(data);
          emitParts();
          break;
        }
        partsBuilder.markLastRootTextFinal();
        emitParts();
        if (tracking) tracking.receivedComplete = true;
        if (data?.result) callbacks.onComplete?.(data.result as never);
        break;
      }

      case 'RUN_ERROR': {
        // A child (sub-agent) failure doesn't end the whole run — only a
        // root-level RUN_ERROR (no parentRunId) is fatal for this stream —
        // but it still closes that child's parts container.
        if (data?.parentRunId) {
          partsBuilder.handleRunEnded(data, 'failed');
          emitParts();
          break;
        }
        const message = typeof data?.message === 'string' ? data.message : 'Stream ended with an error';
        console.warn('[Chat SSE/AGUI] RUN_ERROR:', message);
        callbacks.onError?.(new Error(message));
        break;
      }

      // STEP_FINISHED / TOOL_CALL_END / STATE_SNAPSHOT — no UI surface
      // today, same as the legacy dispatcher silently ignoring their
      // equivalents (tool_call/tool_result/metadata). TOOL_CALL_END itself
      // carries no result — the transcript update happens on the
      // TOOL_CALL_RESULT that immediately follows it.
      default:
        break;
    }
  };
}
