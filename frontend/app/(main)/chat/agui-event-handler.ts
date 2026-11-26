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
import { runOutcome } from './run-outcome';
import type { SSEEvent } from '@/lib/api';
import type {
  AGUIEventEnvelope,
  AGUIJsonPatchOp,
  AttachmentRef,
  ContextCompactedView,
  MessagePart,
  SSEAskUserQuestionEvent,
  SSEInteractionWaitingEvent,
  SSEApprovalRequestedEvent,
  SSEApprovalResolvedEvent,
  SSEArtifactEvent,
  SSEChunkCitation,
  SSEConnectedEvent,
  SSEEngineProgressEvent,
  SSEPlanEvent,
  SSEPlanStep,
} from './types';
import { TOOL_PROGRESS_STATUS } from './types';
import type { StreamMessageCallbacks } from './api';
import { countedApprovals } from './interactions';
import { humanizeToolName, toolStatusLabel } from './utils/tool-display';
import { engineName, isAcpEngine } from '@/app/(main)/workspace/ai-accounts/access-copy';
import { i18n } from '@/lib/i18n';
import { answerErrorText, memoryLimitOf, providerWaitText, sandboxWaitText } from './harness-errors';
import { runFailureWords } from './run-failure-words';
import { toRunModel } from './utils/run-model';
import { ChatStreamError } from './stream-error';
import { toolApprovalDetails } from './tool-approval';
import { toCodeTaskHandoff } from './handoff';
import { connectRequested, connectResolved } from './connect-cards';
import { stepFacts } from './engine-steps';

/** Mutable counters the caller inspects after the stream ends (mirrors the
 * legacy dispatcher's local `receivedComplete`/`lastSSEError` bookkeeping). */
export interface AGUIStreamTracking {
  receivedComplete: boolean;
  /** A root RUN_ERROR arrived and was reported through `onError`. */
  receivedError?: boolean;
}

interface AGUIStreamState {
  citations: SSEChunkCitation[];
  normalizedAnswer: string;
  confidence?: string;
  rawLength: number;
}

/**
 * Everything a handler has built, as plain JSON: a durable stream saves it
 * with the last frame id, so a reloaded page resumes with `Last-Event-ID`
 * instead of replaying the run from its first frame.
 */
export interface AGUIHandlerCheckpoint {
  textBuffer: string;
  rawTextReceived: number;
  state: AGUIStreamState;
  parts: MessagePart[];
  seenIds: string[];
  /** `openTurnParts` keys (`<runId>:text`, `<runId>:reasoning`) still open. */
  openTurnKeys: string[];
  rootRunId: string | null;
  /** Root text parts by message id, as indexes into `parts`. */
  rootTextMessages: Record<string, number>;
  endedRootMessageId: string | null;
  /**
   * Sub-agents' cards still open (UX3). The stream's ids name the cards this
   * reader holds and a resume is not told them again (QA6-F12), so they are
   * saved with the id that names them. Absent in a state saved before that.
   */
  childCards?: SSEApprovalRequestedEvent[];
  /** The root run's latest plan (UXR-B1): a resume after its frame is not told it again. */
  plan?: SSEPlanEvent | null;
}

export interface AGUIHandlerOptions {
  resumeFrom?: AGUIHandlerCheckpoint;
  /**
   * Durable streams only. The server ends a message it can no longer stream
   * (lost buffer, re-claimed run) and restarts it whole under a new id; that
   * restart replaces the ended message instead of settling it as narration.
   * The restart names the message it replaces (`replacesMessageId`); a stream
   * without that marker is read by frame order: an end directly followed by
   * a start with another id.
   */
  replaceRestartedMessages?: boolean;
  onMessageRestarted?: (
    previousMessageId: string,
    messageId: string,
    via: 'marker' | 'order',
  ) => void;
  /** The reader's clock (ms), for the provider wait's countdown; tests inject one. */
  now?: () => number;
}

/** A steering message's files as the frame names them; anything malformed is dropped. */
function steeringAttachments(raw: unknown): AttachmentRef[] {
  if (!Array.isArray(raw)) return [];
  const str = (value: unknown): string => (typeof value === 'string' ? value : '');
  return raw.flatMap((item): AttachmentRef[] => {
    if (!item || typeof item !== 'object') return [];
    const fields = item as Record<string, unknown>;
    const recordId = str(fields.recordId);
    if (!recordId) return [];
    return [{
      recordId,
      virtualRecordId: str(fields.virtualRecordId),
      recordName: str(fields.recordName),
      mimeType: str(fields.mimeType),
      extension: str(fields.extension),
      ...(fields.source === 'upload' || fields.source === 'paste-text' ? { source: fields.source } : {}),
    }];
  });
}

function restartedMessage(
  data: AGUIEventEnvelope | undefined,
  endedRootMessageId: string | null,
  messageId: string | undefined,
): { messageId: string; via: 'marker' | 'order' } | null {
  const marker = data?.replacesMessageId;
  if (typeof marker === 'string' && marker !== '' && marker !== messageId) {
    return { messageId: marker, via: 'marker' };
  }
  if (endedRootMessageId !== null && endedRootMessageId !== messageId) {
    return { messageId: endedRootMessageId, via: 'order' };
  }
  return null;
}

/** A sub-agent's interaction ids are its own run's, so they may repeat another run's (UX3). */
const childCardKey = (card: { interactionId: string; childRunId?: string }): string =>
  `${card.childRunId ?? ''}\u0000${card.interactionId}`;

export type AGUIEventHandler = ((event: SSEEvent) => void) & {
  checkpoint(): AGUIHandlerCheckpoint;
  /** Re-sends the restored parts and answer text to the callbacks. */
  publishView(): void;
};

/**
 * Stream frames the chat hides on purpose (UX-01), keyed `TYPE`, `CUSTOM:<name>` or `STATE_SNAPSHOT:<status>`, with
 * why. Every other frame of `contracts/harness/vectors/ui-stream-events.json` is shown or feeds what is shown.
 */
export const HIDDEN_STREAM_FRAMES: Readonly<Record<string, string>> = Object.freeze({
  STEP_FINISHED: "A sub-agent's end shows from its own RUN_FINISHED or RUN_ERROR.",
  TOOL_CALL_END: 'It carries no result; the step updates on TOOL_CALL_RESULT.',
  REASONING_START: 'The reasoning row opens on REASONING_MESSAGE_START.',
  'CUSTOM:tool_unavailable':
    "The search step's own result names each tool, live and saved alike; its Connect rows are drawn from that (UX-06).",
  'CUSTOM:retrieval_context': 'A measurement frame for evaluation clients, not for people.',
  'CUSTOM:run_usage': 'A measurement frame for evaluation clients, not for people.',
  'STATE_SNAPSHOT:running': "A sub-agent's progress shows on its own card.",
  'STATE_SNAPSHOT:completed': 'The end shows from RUN_FINISHED and the final answer.',
  'STATE_SNAPSHOT:failed': 'The end shows from RUN_ERROR or the error answer.',
  'STATE_SNAPSHOT:cancelled': 'The end shows from RUN_FINISHED with the stopped answer.',
});

// Mirrors the server-side caps in `transcript_collector.py`. A result arrives
// already truncated, but a TOOL_CALL_ARGS `delta` is the call's whole JSON:
// `args` takes the frame's `argsPreview`, and cuts `delta` only from a server
// that does not send one.
const MAX_TOOL_ARGS_CHARS = 2000;
const MAX_TOOL_RESULT_CHARS = 2000;

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

  private rootRunId: string | null = null;

  private textPartsById = new Map<string, MessagePart>();

  /** A durable stream: a child's row names its parent, so it reads the child's own timeline once the stream ends. */
  constructor(private readonly durable = false) {}

  static restore(checkpoint: AGUIHandlerCheckpoint, durable = false): LivePartsBuilder {
    const builder = new LivePartsBuilder(durable);
    const parts = JSON.parse(JSON.stringify(checkpoint.parts)) as MessagePart[];
    builder.parts.push(...parts);
    builder.seenIds = new Set(checkpoint.seenIds);
    builder.rootRunId = checkpoint.rootRunId;
    if (checkpoint.rootRunId) builder.containers.set(checkpoint.rootRunId, builder.parts);
    const visit = (container: MessagePart[]) => {
      for (const part of container) {
        if (part.type === 'tool_call' && part.status === 'running' && part.toolCallId) {
          builder.openToolCalls.set(part.toolCallId, part);
        }
        if (part.type === 'sub_agent' && part.runId && Array.isArray(part.parts)) {
          builder.containers.set(part.runId, part.parts);
          if (part.status === 'running') builder.subAgentParts.set(part.runId, part);
          visit(part.parts);
        }
      }
    };
    visit(builder.parts);
    for (const key of checkpoint.openTurnKeys) {
      const separator = key.lastIndexOf(':');
      const runId = key.slice(0, separator);
      const type = key.slice(separator + 1);
      const container = builder.containerFor(runId || undefined);
      const open = [...container].reverse().find((part) => part.type === type);
      if (open) builder.openTurnParts.set(key, open);
    }
    for (const [messageId, index] of Object.entries(checkpoint.rootTextMessages)) {
      const part = builder.parts[index];
      if (part?.type === 'text') builder.textPartsById.set(messageId, part);
    }
    return builder;
  }

  exportState(): Pick<
    AGUIHandlerCheckpoint,
    'parts' | 'seenIds' | 'openTurnKeys' | 'rootRunId' | 'rootTextMessages'
  > {
    const rootTextMessages: Record<string, number> = {};
    for (const [messageId, part] of this.textPartsById) {
      const index = this.parts.indexOf(part);
      if (index >= 0) rootTextMessages[messageId] = index;
    }
    return {
      parts: JSON.parse(JSON.stringify(this.parts)) as MessagePart[],
      seenIds: [...this.seenIds],
      openTurnKeys: [...this.openTurnParts.keys()],
      rootRunId: this.rootRunId,
      rootTextMessages,
    };
  }

  /** Drops a root text message the server restarted under a new id. */
  removeTextMessage(messageId: string): void {
    const part = this.textPartsById.get(messageId);
    this.textPartsById.delete(messageId);
    if (!part) return;
    const index = this.parts.indexOf(part);
    if (index >= 0) this.parts.splice(index, 1);
    for (const [key, open] of this.openTurnParts) {
      if (open === part) this.openTurnParts.delete(key);
    }
  }
  /** messageId -> where that text message's model call starts in its container. */
  private modelCallStarts = new Map<string, { container: MessagePart[]; index: number }>();

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

  /** Marks the most recent root `text` part as `settled` — i.e. proven to be
   * narration (not the final answer) and safe for `filterRootParts` to show
   * in the activity timeline instead of the live answer buffer. Callers
   * reach this the moment a subsequent root event proves "more work
   * follows" for the turn that just streamed: a tool call, a reasoning
   * block, or a new text turn starting. Returns `true` iff it found and
   * newly settled a part, so callers know whether the live answer buffer
   * mirroring that text needs clearing too — no-ops (returns `false`) if
   * the last text part is already `settled`/`isFinal` or there isn't one,
   * making repeat calls (e.g. a 2nd tool call in the same turn) safe. */
  settleLastRootText(): boolean {
    for (let i = this.parts.length - 1; i >= 0; i -= 1) {
      const part = this.parts[i];
      if (part.type !== 'text') continue;
      if (part.isFinal || part.settled) return false;
      this.parts[i] = { ...part, settled: true };
      return true;
    }
    return false;
  }

  /** A message the user sent while the run worked, where the run took it
   * (`input.consumed`); the saved transcript has the same part there. */
  handleUserInput(data: AGUIEventEnvelope): void {
    const value = (data.value ?? {}) as { messageSeq?: unknown; content?: unknown; attachments?: unknown };
    const messageSeq = typeof value.messageSeq === 'number' ? value.messageSeq : undefined;
    const key = `user_input:${String(messageSeq)}`;
    if (messageSeq === undefined || this.seenIds.has(key)) return;
    this.seenIds.add(key);
    const attachments = steeringAttachments(value.attachments);
    this.parts.push({
      type: 'user_input',
      content: typeof value.content === 'string' ? value.content : '',
      messageSeq,
      ...(attachments.length > 0 ? { attachments } : {}),
      ...(typeof data.runId === 'string' ? { runId: data.runId } : {}),
    });
  }

  /** The /command the run started from (SLASH1 `slash_command` frame): the timeline's first row, once. */
  handleSlashCommand(data: AGUIEventEnvelope): void {
    const value = (data.value ?? {}) as Record<string, unknown>;
    if (typeof value.name !== 'string' || this.seenIds.has('slash_command')) return;
    this.seenIds.add('slash_command');
    this.parts.unshift({
      type: 'slash_command',
      commandName: value.name,
      ...(typeof value.version === 'string' ? { commandVersion: value.version } : {}),
      ...(typeof value.expandedChars === 'number' ? { expandedChars: value.expandedChars } : {}),
      ...(typeof value.owner === 'string' ? { owner: value.owner } : {}),
      ...(typeof data.runId === 'string' ? { runId: data.runId } : {}),
    });
  }

  /** What the person's `/compact` did before the run's first turn (SLASH2 C2 `context_compacted` frame), once. */
  handleContextCompacted(data: AGUIEventEnvelope): void {
    const value = (data.value ?? {}) as Partial<ContextCompactedView>;
    if (typeof value.outcome !== 'string' || this.seenIds.has('context_compacted')) return;
    this.seenIds.add('context_compacted');
    this.parts.unshift({
      type: 'context_compacted',
      compacted: {
        executor: String(value.executor ?? ''),
        outcome: value.outcome,
        beforeTokens: typeof value.beforeTokens === 'number' ? value.beforeTokens : null,
        afterTokens: typeof value.afterTokens === 'number' ? value.afterTokens : null,
        userMessagesKept: value.userMessagesKept === true,
        ...(typeof value.focus === 'string' ? { focus: value.focus } : {}),
      },
      ...(typeof data.runId === 'string' ? { runId: data.runId } : {}),
    });
  }

  /** Something the run tells the person beside its steps (CTL-5b `notice` frame), shown once. */
  handleNotice(data: AGUIEventEnvelope): void {
    const { code, item, kind, engine, reason, instance, tools, writeBy, finishBy, occurrence } = (data.value ?? {}) as {
      code?: unknown;
      item?: unknown;
      kind?: unknown;
      engine?: unknown;
      reason?: unknown;
      instance?: unknown;
      tools?: unknown;
      writeBy?: unknown;
      finishBy?: unknown;
      occurrence?: unknown;
    };
    if (code === 'time_plan') {
      // ADR-RUN-01 D4: the run's time plan; a later turn's replaces the one before it.
      if (typeof writeBy !== 'string' || typeof finishBy !== 'string') return;
      const earlier = this.parts.findIndex((part) => part.type === 'notice' && part.code === 'time_plan');
      if (earlier >= 0) this.parts.splice(earlier, 1);
      this.parts.push({
        type: 'notice',
        code,
        writeBy,
        finishBy,
        ...(typeof data.runId === 'string' ? { runId: data.runId } : {}),
      });
      return;
    }
    // WP-89: by code and occurrence, so a state notice journaled again (a second retire after a resume) shows again.
    const again = typeof occurrence === 'number' && Number.isInteger(occurrence) ? occurrence : undefined;
    const key = `notice:${String(code)}:${String(item)}:${String(again)}`;
    if (typeof code !== 'string' || this.seenIds.has(key)) return;
    this.seenIds.add(key);
    this.parts.push({
      type: 'notice',
      code,
      ...(again !== undefined ? { occurrence: again } : {}),
      ...(typeof item === 'string' ? { item } : {}),
      ...(kind === 'toolset' || kind === 'mcp' ? { kind } : {}),
      ...(typeof engine === 'string' ? { engine } : {}),
      ...(typeof reason === 'string' ? { reason: reason as unknown as MessagePart['reason'] } : {}),
      ...(typeof instance === 'string' ? { instance } : {}),
      ...(Array.isArray(tools) ? { tools: tools.filter((tool): tool is string => typeof tool === 'string') } : {}),
      ...(typeof data.runId === 'string' ? { runId: data.runId } : {}),
    });
  }

  /** An engine run's progress line, folded into its `engine_task` part as the saved answer keeps it (CTL-4). */
  handleEngineProgress(data: AGUIEventEnvelope): void {
    const value = data.value as (SSEEngineProgressEvent & { turnId?: unknown; seq?: unknown }) | undefined;
    if (!value || typeof value.taskId !== 'string' || typeof value.kind !== 'string') return;
    if (typeof value.turnId === 'string' && typeof value.seq === 'number') {
      const key = `engine:${value.taskId}:${value.turnId}:${value.seq}`;
      if (this.seenIds.has(key)) return;
      this.seenIds.add(key);
    }
    const frame: SSEEngineProgressEvent = { tool: value.tool, taskId: value.taskId, kind: value.kind, text: value.text ?? '', ...(value.data ? { data: value.data } : {}) };
    // A code task's child says its steps on its parent's stream, under its own run: they nest under its row.
    const container = this.containerFor(typeof data.runId === 'string' ? data.runId : undefined);
    const task = container.find((part) => part.type === 'engine_task' && part.taskId === value.taskId);
    if (task) task.frames = [...(task.frames ?? []), frame];
    else {
      container.push({
        type: 'engine_task',
        ...(typeof data.runId === 'string' ? { runId: data.runId } : {}),
        taskId: value.taskId,
        frames: [frame],
      });
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
      this.rootRunId = runId;
      return;
    }
    const roleName = this.pendingRoleByParent.get(parentRunId) ?? 'sub-agent';
    this.pendingRoleByParent.delete(parentRunId);
    const model = toRunModel(data.model);
    const codeTaskGoal = typeof data.codeTaskGoal === 'string' ? data.codeTaskGoal : undefined;
    const goal = typeof data.goal === 'string' && data.goal.trim() ? data.goal.trim() : undefined;
    const codeTaskHandoff = toCodeTaskHandoff(data.codeTaskHandoff);
    const subAgentPart: MessagePart = {
      type: 'sub_agent', runId, roleName, parts: [], status: 'running',
      ...(this.durable ? { parentRunId } : {}),
      ...(model ? { model } : {}),
      ...(codeTaskGoal ? { codeTaskGoal } : {}),
      ...(goal ? { goal } : {}),
      ...(codeTaskHandoff ? { codeTaskHandoff } : {}),
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
    if (part) {
      part.status = status;
      // WP62-ERR: the child's machine reason, so the live row words a budget or sign-in stop as a reload does.
      if (status === 'failed' && typeof data.errorType === 'string') part.errorType = data.errorType;
      // R32SECCOMP-FU-4: an out-of-memory end's limit, so the live card names it as a reload does.
      const limit = status === 'failed' ? memoryLimitOf(data.details) : null;
      if (limit !== null) part.memoryLimitMb = limit;
    }
    this.subAgentParts.delete(runId);
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
    if (typeof data.displayName === 'string') {
      part.displayName = data.displayName;
    }
    if (data.approved === true) {
      part.approved = true;
    }
    if (data.approvalAnswer === 'allowed' || data.approvalAnswer === 'declined' || data.approvalAnswer === 'expired') {
      part.approvalAnswer = data.approvalAnswer;
    }
    this.containerFor(runId).push(part);
    this.openToolCalls.set(toolCallId, part);
  }

  handleToolCallArgs(data: AGUIEventEnvelope): void {
    const toolCallId = typeof data.toolCallId === 'string' ? data.toolCallId : undefined;
    if (!toolCallId) return;
    const part = this.openToolCalls.get(toolCallId);
    if (!part) return;
    const delta = typeof data.delta === 'string' ? data.delta : '';
    part.args = typeof data.argsPreview === 'string' ? data.argsPreview : delta.slice(0, MAX_TOOL_ARGS_CHARS);
    if (typeof data.argsSummary === 'string') {
      part.argsSummary = data.argsSummary;
    }
    // The built-in agent's step names its page, query or file as an engine's does (WP-115).
    const step = stepFacts(data.step);
    if (step) part.step = step;
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
    if (typeof data.errorCode === 'string') part.errorCode = data.errorCode;
    if (data.nothingSent === true) part.nothingSent = true;
    // Checked where it is drawn, the same for a live frame and a saved part.
    if (data.resultView !== undefined && data.resultView !== null) part.resultView = data.resultView;
    const step = stepFacts(data.step);
    if (step) part.step = step;
    const approval = status === 'awaiting_approval' ? toolApprovalDetails(data.approval) : null;
    if (approval) part.approval = approval;
  }

  /** Read-only peek at the dedup set `handleTextStart` below consults —
   * lets the event handler tell a genuinely new root text turn apart from
   * a duplicate replay (same `messageId`) BEFORE calling `handleTextStart`,
   * so it only settles/clears the previous turn's buffer when a new part
   * is actually about to be pushed (a duplicate leaves the previously open
   * part's object reference untouched, which settling would break). */
  hasSeenMessage(messageId: string | undefined): boolean {
    return typeof messageId === 'string' && this.seenIds.has(messageId);
  }

  handleTextStart(data: AGUIEventEnvelope): void {
    const messageId = typeof data.messageId === 'string' ? data.messageId : undefined;
    if (messageId) {
      if (this.seenIds.has(messageId)) return;
      this.seenIds.add(messageId);
    }
    const runId = typeof data.runId === 'string' ? data.runId : undefined;
    const part: MessagePart = { type: 'text', content: '', runId };
    const container = this.containerFor(runId);
    if (messageId) this.modelCallStarts.set(messageId, { container, index: container.length });
    container.push(part);
    this.openTurnParts.set(`${runId ?? ''}:text`, part);
    if (messageId) this.textPartsById.set(messageId, part);
  }

  /** Mirrors `TranscriptCollector`: a model call that failed mid-stream is
   * dropped whole — its text message and the reasoning streamed after it. */
  discardMessage(messageId: string | undefined, runId: string | undefined): void {
    if (!messageId) return;
    const start = this.modelCallStarts.get(messageId);
    this.modelCallStarts.delete(messageId);
    this.textPartsById.delete(messageId);
    this.openTurnParts.delete(`${runId ?? ''}:text`);
    this.openTurnParts.delete(`${runId ?? ''}:reasoning`);
    if (start) start.container.splice(start.index);
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
 * normalizedAnswer, rawLength}`. Not a general JSON Patch implementation —
 * PipesHub's server only ever patches these single-level paths.
 */
function applyStatePatch(state: AGUIStreamState, ops: AGUIJsonPatchOp[]): AGUIStreamState {
  let next = state;
  for (const op of ops) {
    if (op.op !== 'replace' && op.op !== 'add') continue;
    if (op.path === '/citations' && Array.isArray(op.value)) {
      next = { ...next, citations: op.value as SSEChunkCitation[] };
    } else if (op.path === '/normalizedAnswer' && typeof op.value === 'string') {
      next = { ...next, normalizedAnswer: op.value };
    } else if (op.path === '/confidence' && (typeof op.value === 'string' || op.value === null)) {
      next = { ...next, confidence: (op.value as string | null) ?? undefined };
    } else if (op.path === '/rawLength' && typeof op.value === 'number') {
      next = { ...next, rawLength: op.value };
    }
  }
  return next;
}

const PLAN_STATUSES: ReadonlySet<string> = new Set(['pending', 'in_progress', 'completed', 'cancelled']);

/** A `plan` frame's value, or null when it is not one (a newer server's shape is never guessed at). */
export function planOf(value: unknown): SSEPlanEvent | null {
  if (!value || typeof value !== 'object') return null;
  const { runId, steps, currentTaskId } = value as Record<string, unknown>;
  if (typeof runId !== 'string' || !Array.isArray(steps)) return null;
  const valid = steps.filter(
    (step): step is SSEPlanStep =>
      !!step &&
      typeof step === 'object' &&
      typeof (step as SSEPlanStep).taskId === 'string' &&
      typeof (step as SSEPlanStep).title === 'string' &&
      PLAN_STATUSES.has((step as SSEPlanStep).status),
  );
  return {
    runId,
    steps: valid.map((step) => ({ ...step, deps: Array.isArray(step.deps) ? step.deps : [] })),
    currentTaskId: typeof currentTaskId === 'string' ? currentTaskId : null,
  };
}

/**
 * What an MCP server said about a running tool (`AGUIFormatter.tool_progress`), shown after its
 * label: its own message, else "3/10", or a percentage for fractional progress.
 */
function toolProgressText(snapshot: Record<string, unknown>): string {
  const note = typeof snapshot.progress_message === 'string' ? snapshot.progress_message.trim() : '';
  if (note) return ` ${note}`;
  const { progress, total } = snapshot;
  if (typeof progress !== 'number' || typeof total !== 'number' || !Number.isFinite(progress) || !(total > 0)) {
    return '';
  }
  if (Number.isInteger(progress) && Number.isInteger(total)) return ` ${progress}/${total}`;
  return ` ${Math.round(Math.min(Math.max(progress / total, 0), 1) * 100)}%`;
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
  options: AGUIHandlerOptions = {},
): AGUIEventHandler {
  const resume = options.resumeFrom;
  let textBuffer = resume?.textBuffer ?? '';
  let rawTextReceived = resume?.rawTextReceived ?? 0;
  let awaitingRetryAfterDiscard = false;
  let state: AGUIStreamState = resume
    ? { ...resume.state }
    : { citations: [], normalizedAnswer: '', rawLength: 0 };
  let endedRootMessageId = resume?.endedRootMessageId ?? null;
  let plan = resume?.plan ?? null;
  const durable = options.replaceRestartedMessages === true;
  const partsBuilder = resume ? LivePartsBuilder.restore(resume, durable) : new LivePartsBuilder(durable);
  const emitParts = () => callbacks.onParts?.(partsBuilder.snapshot());
  const childCards = new Map<string, SSEApprovalRequestedEvent>(
    (resume?.childCards ?? []).map((card) => [childCardKey(card), card]),
  );
  const requested = (card: SSEApprovalRequestedEvent) => {
    if (card.childRunId) childCards.set(childCardKey(card), card);
    callbacks.onApprovalRequested?.(card);
  };
  const resolved = (decision: SSEApprovalResolvedEvent) => {
    if (decision.childRunId) childCards.delete(childCardKey(decision));
    callbacks.onApprovalResolved?.(decision);
  };

  const handle = (event: SSEEvent) => {
    const data = event.data as AGUIEventEnvelope | undefined;
    const type = data?.type ?? event.event;
    // A frame this tab does not know (a newer Node's, VS-10) changes nothing, not even the END/START adjacency.
    let known = true;

    switch (type) {
      case 'STEP_STARTED': {
        const stepName = typeof data?.stepName === 'string' ? data.stepName : '';
        if (stepName.startsWith('sub_agent:')) {
          const engine = typeof data?.childEngine === 'string' ? data.childEngine : '';
          // A code task's child is its engine's run: named as its card names it ("Codex wants to:").
          const roleName = engine
            ? i18n.t(`interaction.engine.${engine}`, {
                defaultValue: isAcpEngine(engine) ? engineName(engine) : humanizeToolName(engine),
              })
            : stepName.slice('sub_agent:'.length);
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
        // TerminalAnswerStreamer's own per-turn buffer reset (plan 1e). If
        // the previous root turn's text is still live in the answer buffer,
        // a genuinely NEW turn starting proves it was narration (RUN_FINISHED
        // would have landed instead of another TEXT_MESSAGE_START if it were
        // the final answer) — settle it into the timeline and clear the
        // buffer in lockstep so it isn't shown in both places at once.
        // Both this settle-and-clear and the plain reset below are gated on
        // `!hasSeenMessage` because a duplicate replay (same `messageId`)
        // leaves the currently-open part's object reference untouched
        // (`handleTextStart` no-ops on it), so `textBuffer` must also stay
        // untouched — resetting it would make the next TEXT_MESSAGE_CONTENT
        // report `accumulated` as just the new delta, dropping everything
        // already streamed for this turn.
        const runId = typeof data?.runId === 'string' ? data.runId : undefined;
        const messageId = typeof data?.messageId === 'string' ? data.messageId : undefined;
        const isNewRootTurn = partsBuilder.isRootRun(runId) && !partsBuilder.hasSeenMessage(messageId);
        const replaced =
          options.replaceRestartedMessages === true && isNewRootTurn
            ? restartedMessage(data, endedRootMessageId, messageId)
            : null;
        if (replaced !== null) {
          partsBuilder.removeTextMessage(replaced.messageId);
          options.onMessageRestarted?.(replaced.messageId, messageId ?? '', replaced.via);
          state = { ...state, normalizedAnswer: '', rawLength: 0 };
          callbacks.onChunk?.({ chunk: '', accumulated: '', citations: state.citations });
        } else if (isNewRootTurn && textBuffer) {
          partsBuilder.settleLastRootText();
          state = { ...state, normalizedAnswer: '', rawLength: 0 };
          callbacks.onChunk?.({ chunk: '', accumulated: '', citations: state.citations });
        }
        if (isNewRootTurn) {
          textBuffer = '';
          rawTextReceived = 0;
          awaitingRetryAfterDiscard = false;
        }
        endedRootMessageId = null;
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
          rawTextReceived += delta.length;
          callbacks.onChunk?.({ chunk: delta, accumulated: textBuffer, citations: state.citations });
        }
        if (data) partsBuilder.handleTextContent(data);
        emitParts();
        break;
      }

      case 'TEXT_MESSAGE_END': {
        if (data) partsBuilder.handleTextEnd(data);
        const endedId = typeof data?.messageId === 'string' ? data.messageId : null;
        endedRootMessageId =
          endedId !== null && partsBuilder.isRootRun(typeof data?.runId === 'string' ? data.runId : undefined)
            ? endedId
            : null;
        // After a text turn ends there is often a multi-second gap before the
        // next TOOL_CALL_START arrives (the LLM is still generating tool-call
        // arguments server-side). Show "Thinking…" so the chat doesn't look
        // idle. We do NOT clear the text buffer here because we can't yet
        // tell whether this text is narration (tool call follows) or the
        // final answer (RUN_FINISHED follows) — TOOL_CALL_START clears it.
        const runId = typeof data?.runId === 'string' ? data.runId : undefined;
        if (partsBuilder.isRootRun(runId)) {
          callbacks.onStatus?.({ status: 'calling_llm', message: 'Thinking...' });
        }
        emitParts();
        break;
      }

      case 'REASONING_MESSAGE_START': {
        // A reasoning block starting proves the text turn that just streamed
        // was narration, not the final answer (mirrors the TOOL_CALL_START
        // handling below) — settle it into the timeline and clear the live
        // answer buffer so it isn't shown in both places while the model
        // reasons before its next turn.
        const runId = typeof data?.runId === 'string' ? data.runId : undefined;
        if (textBuffer && partsBuilder.isRootRun(runId)) {
          partsBuilder.settleLastRootText();
          textBuffer = '';
          rawTextReceived = 0;
          state = { ...state, normalizedAnswer: '', rawLength: 0 };
          callbacks.onChunk?.({ chunk: '', accumulated: '', citations: state.citations });
        }
        if (data) partsBuilder.handleReasoningStart(data);
        emitParts();
        break;
      }

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
        emitParts();
        break;

      case 'REASONING_END':
        callbacks.onReasoning?.({ delta: '', done: true });
        break;

      case 'TOOL_CALL_START': {
        const toolCallName = typeof data?.toolCallName === 'string' ? data.toolCallName : 'tool';
        // Present tense: a written `displayName` is past tense ("Ran code") and would make a long-running tool look
        // finished; only its app and tool words are taken ("E2Esaas · Create ticket").
        const shownName = typeof data?.displayName === 'string' ? data.displayName : undefined;
        callbacks.onStatus?.({ status: 'executing', message: `${toolStatusLabel(toolCallName, shownName)}...` });
        // A tool call is about to run for the turn that was just streamed —
        // it was never the final answer, so settle it into the timeline
        // (already appended as a `text` part above) and stop showing it in
        // the live answer buffer. Mirrors `TerminalAnswerStreamer.
        // _clear_preamble` server-side; no-ops on the 2nd+ tool call in the
        // same turn (buffer already cleared) and on child tool calls
        // (their own agent's answer buffer is untouched by a delegate).
        const runId = typeof data?.runId === 'string' ? data.runId : undefined;
        if (textBuffer && partsBuilder.isRootRun(runId)) {
          partsBuilder.settleLastRootText();
          textBuffer = '';
          rawTextReceived = 0;
          state = { ...state, normalizedAnswer: '', rawLength: 0 };
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

      case 'TOOL_CALL_RESULT': {
        if (data) partsBuilder.handleToolCallResult(data);
        emitParts();
        // Tool finished; the next LLM turn can take seconds before
        // STATE_SNAPSHOT(calling_llm) / TEXT_MESSAGE_START arrives.
        const runId = typeof data?.runId === 'string' ? data.runId : undefined;
        if (partsBuilder.isRootRun(runId)) {
          callbacks.onStatus?.({ status: 'calling_llm', message: 'Thinking...' });
        }
        break;
      }

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
            if (op.value) callbacks.onArtifact?.(op.value as SSEArtifactEvent);
          } else {
            textOps.push(op);
          }
        }
        if (textOps.length === 0) break;
        // The answer streamer runs behind these frames, so snapshots of the
        // discarded attempt can follow `message_discarded`; the one marked
        // `/discarded` is the first written after it.
        const endsDiscard = textOps.some((op) => op.path === '/discarded');
        if (awaitingRetryAfterDiscard && !endsDiscard) break;
        state = applyStatePatch(state, textOps);
        if (endsDiscard) {
          awaitingRetryAfterDiscard = false;
          textBuffer = state.normalizedAnswer;
          callbacks.onChunk?.({ chunk: '', accumulated: textBuffer, citations: state.citations });
          break;
        }
        const normalizationCurrent =
          state.rawLength > 0
            ? state.rawLength >= rawTextReceived
            : state.normalizedAnswer.length >= textBuffer.length;
        if (normalizationCurrent) {
          textBuffer = state.normalizedAnswer;
        }
        callbacks.onChunk?.({
          chunk: '',
          accumulated: textBuffer,
          citations: state.citations,
          ...(state.confidence !== undefined ? { confidence: state.confidence } : {}),
        });
        break;
      }

      case 'CUSTOM': {
        const name = data?.name;
        const value = data?.value;
        if (name === 'conversation_created') {
          callbacks.onConnected?.((value ?? {}) as SSEConnectedEvent);
        } else if (name === 'ask_user_question') {
          callbacks.onAskUserQuestion?.(value as SSEAskUserQuestionEvent);
        } else if (name === 'approval_requested') {
          requested(value as SSEApprovalRequestedEvent);
        } else if (name === 'approval_resolved') {
          resolved(value as SSEApprovalResolvedEvent);
        } else if (name === 'approval_counted' && value && typeof value === 'object') {
          const { interactionId, approvals } = value as { interactionId?: unknown; approvals?: unknown };
          const counted = countedApprovals(approvals);
          if (typeof interactionId === 'string' && counted !== undefined) {
            callbacks.onApprovalCounted?.({ interactionId, approvals: counted });
          }
        } else if (name === 'run_cost') {
          callbacks.onRunCost?.(value);
        } else if (name === 'interaction_waiting' && value && typeof value === 'object') {
          const waiting = value as Partial<SSEInteractionWaitingEvent>;
          callbacks.onInteractionWaiting?.({ count: Number(waiting.count) || 0, paused: waiting.paused === true });
        } else if (name === 'unknown_effect' && value && typeof value === 'object') {
          // An action that may or may not have happened holds the run for a person (UX3).
          requested({
            ...(value as Omit<SSEApprovalRequestedEvent, 'args' | 'kind'>),
            args: '',
            kind: 'unknown_effect',
          });
        } else if (name === 'unknown_effect_resolved') {
          resolved(value as SSEApprovalResolvedEvent);
        } else if (name === 'connect_requested') {
          // G-12: a call waits for the owner to connect an item; its card sits where an approval would.
          const card = connectRequested(value);
          if (card) requested(card);
        } else if (name === 'connect_resolved') {
          const decision = connectResolved(value);
          if (decision) resolved(decision);
        } else if (name === 'artifact') {
          if (value) callbacks.onArtifact?.(value as SSEArtifactEvent);
        } else if (name === 'turn_started') {
          callbacks.onTurnStarted?.((value ?? {}) as { conversationId?: string; userMessageId?: string });
        } else if (name === 'engine_progress') {
          if (value && typeof value === 'object' && data) {
            partsBuilder.handleEngineProgress(data);
            emitParts();
          }
        } else if (name === 'plan') {
          const shown = planOf(value);
          if (shown && partsBuilder.isRootRun(shown.runId)) {
            plan = shown;
            callbacks.onPlan?.(shown);
          }
        } else if (name === 'user_input' && data) {
          // The run took a message the user sent while it worked: whatever
          // streamed before was not the answer (a new turn follows).
          if (textBuffer) {
            partsBuilder.settleLastRootText();
            textBuffer = '';
            rawTextReceived = 0;
            state = { ...state, normalizedAnswer: '', rawLength: 0 };
            callbacks.onChunk?.({ chunk: '', accumulated: '', citations: state.citations });
          }
          partsBuilder.handleUserInput(data);
          emitParts();
        } else if (name === 'notice' && data) {
          partsBuilder.handleNotice(data);
          emitParts();
        } else if (name === 'context_compacted' && data) {
          if (partsBuilder.isRootRun(data.runId as string | undefined)) {
            partsBuilder.handleContextCompacted(data);
            emitParts();
          }
        } else if (name === 'slash_command' && data) {
          if (partsBuilder.isRootRun(data.runId as string | undefined)) {
            partsBuilder.handleSlashCommand(data);
            emitParts();
          }
        } else if (name === 'message_discarded') {
          const runId = typeof data?.runId === 'string' ? data.runId : undefined;
          const discarded = (value ?? {}) as { messageId?: unknown };
          partsBuilder.discardMessage(
            typeof discarded.messageId === 'string' ? discarded.messageId : undefined,
            runId,
          );
          if (partsBuilder.isRootRun(runId)) {
            textBuffer = '';
            rawTextReceived = 0;
            awaitingRetryAfterDiscard = true;
            state = { ...state, normalizedAnswer: '', rawLength: 0 };
            callbacks.onChunk?.({ chunk: '', accumulated: '', citations: state.citations });
          }
          emitParts();
        } else {
          // A name of HIDDEN_STREAM_FRAMES, or one this tab does not know: it changes nothing.
          known = false;
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
        callbacks.onAnswerFinal?.();
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
        const errorCode = typeof data?.code === 'string' ? data.code : null;
        // WP-100: worded from the run's typed outcome (sent to this client, X-PipesHub-Frames: 2) as the saved
        // failure is; the frame's own fields from a Node before it.
        const readable = runFailureWords(errorCode, runOutcome(data?.runOutcome), {
          servers: data?.servers,
          details: data?.details,
        });
        const message =
          readable !== null ? readable : typeof data?.message === 'string' ? data.message : 'Stream ended with an error';
        // A Stop can surface as RUN_ERROR{code:'abort'} instead of a clean
        // RUN_FINISHED{status:'stopped'}; its raw message is no reply to show.
        // `receivedError` stays unset so `runChatStream` still ends the turn as
        // interrupted when the stream closes: without a Stop no grace timer
        // exists to settle the slot, and with one the slot ignores that error.
        if (data?.code === 'abort') {
          console.warn('[Chat SSE/AGUI] RUN_ERROR with abort code — treating as stop, not error:', message);
          break;
        }
        if (tracking) tracking.receivedError = true;
        console.warn('[Chat SSE/AGUI] RUN_ERROR:', message);
        callbacks.onError?.(
          new ChatStreamError(message, typeof data?.code === 'string' ? data.code : undefined, data?.details)
        );
        break;
      }

      case 'STATE_SNAPSHOT': {
        const snapshot = data?.snapshot as Record<string, unknown> | undefined;
        // `AnswerFinalizer`'s authoritative snapshot (see
        // `AGUIFormatter.answer_final`) carries no progress status — it means
        // the answer is settled, so no further "working" indicator is due.
        if (snapshot?.final === true) {
          // An error answer is shown in the reader's words for its code, never the answer's English (QA17-O2).
          const localized =
            snapshot.answerMatchType === 'Error' ? answerErrorText(snapshot.errorCode) : null;
          if (localized !== null) {
            textBuffer = localized;
            callbacks.onChunk?.({ chunk: '', accumulated: localized, citations: [] });
          }
          callbacks.onAnswerFinal?.();
          break;
        }
        const status = typeof snapshot?.status === 'string' ? snapshot.status : '';
        const STATUS_MESSAGES: Record<string, string> = {
          queued: i18n.t('chatStream.queued'),
          starting: 'Starting...',
          calling_llm: 'Thinking...',
          running_tool: 'Executing tool...',
          waiting_provider: providerWaitText(snapshot?.retry_at, (options.now ?? Date.now)()),
          waiting_sandbox: sandboxWaitText(snapshot?.retry_at, (options.now ?? Date.now)()),
          waiting_store: i18n.t('chatStream.storeWait'),
          // ADR-OPS-01 D6: its coding engine version was retired; the notice beside it says what happens next.
          waiting_engine_version: i18n.t('chatStream.engineVersionWait'),
        };
        let message = STATUS_MESSAGES[status];
        if (message) {
          let shownStatus = status;
          if (status === 'running_tool' && typeof snapshot?.current_tool === 'string') {
            const progress = toolProgressText(snapshot);
            message = `${toolStatusLabel(snapshot.current_tool)}...${progress}`;
            // The tool's own progress ("waiting in queue", "3/10") says more than its
            // running row does, so the activity timeline keeps it visible.
            if (progress) shownStatus = TOOL_PROGRESS_STATUS;
          }
          callbacks.onStatus?.({ status: shownStatus, message });
        }
        break;
      }

      // Hidden on purpose (HIDDEN_STREAM_FRAMES): like a frame this tab does not know, they change nothing.
      case 'STEP_FINISHED':
      case 'TOOL_CALL_END':
      case 'REASONING_START':
        known = false;
        break;

      default:
        known = false;
        break;
    }
    if (known && type !== 'TEXT_MESSAGE_START' && type !== 'TEXT_MESSAGE_END') endedRootMessageId = null;
  };

  return Object.assign(handle, {
    checkpoint: (): AGUIHandlerCheckpoint => ({
      textBuffer,
      rawTextReceived,
      state: { ...state },
      endedRootMessageId,
      ...partsBuilder.exportState(),
      childCards: [...childCards.values()],
      plan,
    }),
    publishView: () => {
      emitParts();
      for (const card of childCards.values()) callbacks.onApprovalRequested?.(card);
      if (plan) callbacks.onPlan?.(plan);
      if (textBuffer || state.citations.length > 0) {
        callbacks.onChunk?.({
          chunk: '',
          accumulated: textBuffer,
          citations: state.citations,
          ...(state.confidence !== undefined ? { confidence: state.confidence } : {}),
        });
      }
    },
  });
}
