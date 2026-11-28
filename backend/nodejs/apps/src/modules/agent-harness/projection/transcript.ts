import {
  operatorStopOutcome,
  type OperatorStop,
} from '../domain/operator-controls';
import type { AnswerErrorCode } from '../contracts/generated/ext/pipeshub/enums';
import { RunTranscript } from '../ports/conversation-writer';
import { JournalEvent } from '../domain/journal';
import { GVISOR_REQUIRED_MESSAGE } from '../domain/engine-isolation';
import { codePoints } from './text-filter';
import {
  AnswerRecord,
  ApprovalView,
  ArtifactView,
  CallView,
  childModelLabel,
  ChildView,
  ConnectStatus,
  ConnectView,
  EngineTaskView,
  foldTimeline,
  InputView,
  ModelLabel,
  savedArgs,
  Timeline,
} from './timeline';

/**
 * The legacy transcript shapes (REPORT 8) from a folded timeline:
 * `parts[]` the way `TranscriptCollector` built them, and the
 * `completion_data` the way `AnswerFinalizer` shaped it. The final
 * `STATE_SNAPSHOT` and the conversation saved in the commit transaction are
 * both this object (L-6).
 */

export type MessagePart = Record<string, unknown>;

/** `TranscriptCollector` bounds a result preview to this many characters (`tool_loop.RESULT_PREVIEW_CHARS`). */
const RESULT_PREVIEW_CHARS = 2000;

const preview = (content: string): string =>
  codePoints(content).slice(0, RESULT_PREVIEW_CHARS).join('');

/** The /command the run started from (SLASH1): the timeline's first row. */
export function commandParts(timeline: Timeline): MessagePart[] {
  const command = timeline.command;
  return command === undefined
    ? []
    : [
        {
          type: 'slash_command',
          commandName: command.name,
          commandVersion: command.version,
          expandedChars: command.expandedChars,
          owner: command.owner,
          runId: timeline.runId,
        },
      ];
}

/** What the person's `/compact` did before the run's first turn (SLASH2 C2): the chat's saved row. */
export function compactedParts(timeline: Timeline): MessagePart[] {
  const compacted = timeline.compacted;
  return compacted === undefined
    ? []
    : [{ type: 'context_compacted', compacted, runId: timeline.runId }];
}

/** A steering message as a part, where the run took it (or at the end, untaken). */
export function inputPart(input: InputView, runId: string): MessagePart {
  return {
    type: 'user_input',
    content: input.text,
    messageSeq: input.messageSeq,
    runId,
    ...(input.attachments !== undefined
      ? { attachments: input.attachments }
      : {}),
    ...(input.afterTurns === null ? { consumed: false } : {}),
  };
}

/**
 * Every steering message of the run in queue order, taken or not: what a
 * transcript with no parts of its own (a failure, a pre-run question) keeps,
 * so a message the user sent is never lost (C-21).
 */
export function inputParts(timeline: Timeline): MessagePart[] {
  return timeline.inputs.map((input) => inputPart(input, timeline.runId));
}

/**
 * What the person reads of a failed call (ERR-UX): its code, and whether nothing was sent (a write or execute
 * whose outcome is a definite error). Never its detail, which is the model's and the owner's activity panel's.
 */
export function failureFields(call: CallView): {
  errorCode?: string;
  nothingSent?: true;
} {
  if (call.errorCode === undefined) return {};
  return {
    errorCode: call.errorCode,
    ...(call.writes === true ? { nothingSent: true as const } : {}),
  };
}

export function toolPart(call: CallView, runId: string): MessagePart {
  const part: MessagePart = {
    type: 'tool_call',
    toolCallId: call.callId,
    toolName: call.name,
    args: savedArgs(call),
    status: call.status ?? 'running',
    runId,
  };
  if (call.displayName !== undefined) part['displayName'] = call.displayName;
  const took = between(call.startedAt, call.endedAt);
  if (took !== undefined) part['durationMs'] = took;
  if (call.argsSummary !== undefined) part['argsSummary'] = call.argsSummary;
  if (call.step !== undefined) part['step'] = { ...call.step };
  if (call.status === 'blocked' && call.blockedBy !== undefined) {
    part['blockedBy'] = call.blockedBy;
  }
  // G-03: a dry run's call, recorded as what the run would have done and never made.
  if (call.shadow !== undefined) part['dryRun'] = { ...call.shadow };
  Object.assign(part, failureFields(call));
  if (call.status !== undefined && call.content !== undefined) {
    part['resultPreview'] = preview(call.saved?.content ?? call.content);
    if (call.status !== 'blocked' && call.resultSummary !== undefined) {
      part['resultSummary'] = call.saved?.resultSummary ?? call.resultSummary;
    }
    if (call.status !== 'blocked' && call.artifactId !== undefined) {
      part['artifactId'] = call.artifactId;
    }
    if (call.status === 'completed' && call.resultView !== undefined) {
      part['resultView'] = call.resultView;
    }
  }
  return part;
}

/** The engine's task as a part: its progress lines, for the card a reload rebuilds (U3). */
export function engineTaskPart(
  engine: EngineTaskView,
  runId: string,
): MessagePart {
  return {
    type: 'engine_task',
    runId,
    taskId: engine.taskId,
    frames: engine.frames,
    ...(engine.truncated ? { truncated: true } : {}),
  };
}

export function approvalPart(
  approval: ApprovalView,
  runId: string,
): MessagePart {
  return {
    type: 'approval',
    interactionId: approval.interactionId,
    toolCallId: approval.toolCallId,
    toolName: approval.toolName,
    ...(approval.displayName !== undefined
      ? { displayName: approval.displayName }
      : {}),
    args: approval.args,
    ...(approval.argsSummary !== undefined
      ? { argsSummary: approval.argsSummary }
      : {}),
    status: approval.status,
    ...(approval.reason !== undefined ? { reason: approval.reason } : {}),
    ...(approval.scope !== undefined ? { scope: approval.scope } : {}),
    ...(approval.effect !== undefined ? { effect: approval.effect } : {}),
    ...(approval.denyReason !== undefined
      ? { denyReason: approval.denyReason }
      : {}),
    ...(approval.editedArgs !== undefined
      ? { editedArgs: approval.editedArgs }
      : {}),
    // UXINT-1: the chat anchors and orders the card by these, live and saved alike.
    seq: approval.seq,
    afterTurns: approval.afterTurns,
    decidedBefore: approval.decidedBefore,
    ...(approval.stepId !== undefined ? { stepId: approval.stepId } : {}),
    ...(approval.requestedAt !== undefined
      ? { requestedAt: approval.requestedAt }
      : {}),
    runId,
  };
}

/** A connect wait's status as the saved reply's approval part says it: the chat words both the same way. */
const CONNECT_PART_STATUS: Readonly<Record<ConnectStatus, string>> = {
  pending: 'pending',
  connected: 'approved',
  declined: 'denied',
  expired: 'expired',
  closed: 'closed',
};

/**
 * A call that waited for the owner to connect an item (G-12), as an approval part of kind `connect`: the chat anchors,
 * orders and receipts it as it does an approval card. A shared reader may see the reply, so the instance stays off it.
 */
export function connectPart(connect: ConnectView, runId: string): MessagePart {
  return {
    type: 'approval',
    kind: 'connect',
    interactionId: connect.interactionId,
    toolCallId: connect.toolCallId,
    ...(connect.toolName !== undefined ? { toolName: connect.toolName } : {}),
    ...(connect.displayName !== undefined
      ? { displayName: connect.displayName }
      : {}),
    connect: {
      item: connect.item,
      itemKind: connect.itemKind,
      connectPath: connect.connectPath,
      ...(connect.reason !== undefined
        ? { reason: connect.reason, since: connect.askedAt }
        : {}),
    },
    status: CONNECT_PART_STATUS[connect.status],
    seq: connect.seq,
    afterTurns: connect.afterTurns,
    ...(connect.stepId !== undefined ? { stepId: connect.stepId } : {}),
    runId,
  };
}

/** A child as the legacy `sub_agent` part; its own steps are read from its own stream. */
export function childPart(
  child: ChildView,
  parentRunId: string,
  model?: ModelLabel,
): MessagePart {
  return {
    type: 'sub_agent',
    runId: child.runId,
    parentRunId,
    roleName: child.role,
    ...(child.codeTaskGoal !== undefined
      ? { codeTaskGoal: child.codeTaskGoal }
      : {}),
    ...(child.goal !== undefined ? { goal: child.goal } : {}),
    ...(child.codeTaskHandoff !== undefined
      ? { codeTaskHandoff: { ...child.codeTaskHandoff } }
      : {}),
    status:
      child.status === 'running' || child.status === 'completed'
        ? child.status
        : 'failed',
    parts: [],
    ...(child.status === 'cancelled'
      ? { errorType: 'cancelled' }
      : child.errorType !== undefined
        ? { errorType: child.errorType }
        : {}),
    // R32SECCOMP-FU-4: the job card says "ran out of memory (limit 4 GB)", as the run's own error does.
    ...(child.status === 'failed' && child.memoryLimitMb !== undefined
      ? { memoryLimitMb: child.memoryLimitMb }
      : {}),
    ...(model !== undefined
      ? { model: { provider: model.provider, model: model.model } }
      : {}),
  };
}

/**
 * Milliseconds from one journal time to a later one (CTL-3b); undefined when either is missing or no time passed
 * between their commits, so a part carries a duration only when there is one to show.
 */
function between(
  from: string | undefined,
  to: string | undefined,
): number | undefined {
  if (from === undefined || to === undefined) return undefined;
  const ms = Date.parse(to) - Date.parse(from);
  return Number.isFinite(ms) && ms > 0 ? ms : undefined;
}

/** The run's plan as it stood last, for the checklist a reload shows (CTL-3b). */
function planPart(timeline: Timeline): MessagePart | null {
  if (timeline.tasks.length === 0) return null;
  return {
    type: 'plan',
    runId: timeline.runId,
    steps: timeline.tasks.map(({ taskId, title, status, deps }) => ({
      taskId,
      title,
      status,
      deps,
    })),
  };
}

/** `parts[]` of the run: one text part per model turn, then its visible tool calls. */
export function buildParts(timeline: Timeline): MessagePart[] {
  const runId = timeline.runId;
  const parts: MessagePart[] = [];
  const textParts: MessagePart[] = [];
  const reasoning = new Map<number, unknown>(
    (timeline.answer?.reasoning ?? []).map((entry) => [
      Number(entry['turnIndex']),
      entry['content'],
    ]),
  );
  let continues = false;
  const happened = [
    ...timeline.approvals.map((view) => ({
      at: view.afterTurns,
      seq: view.seq,
      part: approvalPart(view, runId),
    })),
    ...timeline.connects.map((view) => ({
      at: view.afterTurns,
      seq: view.seq,
      part: connectPart(view, runId),
    })),
    ...timeline.children.map((view) => ({
      at: view.afterTurns,
      seq: view.seq,
      part: childPart(view, runId, childModelLabel(timeline, view)),
    })),
  ].sort((a, b) => a.seq - b.seq);
  const inputsAfter = (turns: number | null): void => {
    for (const input of timeline.inputs) {
      if (input.afterTurns === turns) parts.push(inputPart(input, runId));
    }
    for (const entry of happened) {
      if (entry.at === turns) parts.push(entry.part);
    }
  };
  parts.push(...commandParts(timeline));
  parts.push(...compactedParts(timeline));
  for (const notice of timeline.notices)
    parts.push({ type: 'notice', ...notice, runId });
  const plan = planPart(timeline);
  if (plan !== null) parts.push(plan);
  if (timeline.engine !== null) {
    parts.push(engineTaskPart(timeline.engine, runId));
  }
  inputsAfter(0);
  timeline.turns.forEach((turn, index) => {
    const previous = textParts[textParts.length - 1];
    const joins =
      continues && turn.calls.length === 0 && previous !== undefined;
    continues = turn.truncated === true && turn.calls.length === 0;
    if (turn.synthetic === true) {
      // No model spoke: legacy's collector holds no text for it, and `partTexts` counts model turns only.
    } else if (joins) {
      // Legacy's collector joins a reply that continues a cut-off one
      // into the cut-off part (`joins_truncated`).
      previous['content'] = `${String(previous['content'])}${turn.text}`;
    } else {
      const text: MessagePart = { type: 'text', content: turn.text, runId };
      const took = between(turn.startedAt, turn.respondedAt);
      if (took !== undefined) text['durationMs'] = took;
      parts.push(text);
      textParts.push(text);
    }
    // Legacy persists what it streamed as reasoning, truncated and gated the
    // way the finalizer's `completion.reasoning` is: that record is the source.
    const thought = reasoning.get(turn.turn - 1);
    if (typeof thought === 'string' && thought.length > 0) {
      parts.push({ type: 'reasoning', content: thought, runId });
    }
    for (const call of turn.calls) {
      if (!call.hidden) parts.push(toolPart(call, runId));
    }
    const before = parts.length;
    inputsAfter(index + 1);
    // The user spoke: what the model writes next is a reply, not the cut-off text going on.
    if (parts.length > before) continues = false;
  });
  if (failedModelCall(timeline)) {
    // Legacy opened the failed call's message before the model refused it,
    // so the error answer replaces that (empty) text part.
    const text: MessagePart = { type: 'text', content: '', runId };
    parts.push(text);
    textParts.push(text);
  }
  const answer = finalAnswer(timeline);
  if (answer !== null) {
    textParts.forEach((part, index) => {
      const normalized = answer.partTexts[index];
      if (normalized !== undefined) part['content'] = normalized;
    });
    const last = textParts[textParts.length - 1];
    let final: MessagePart;
    if (answer.partTexts.length > textParts.length || last === undefined) {
      final = { type: 'text', content: answer.answer, isFinal: true };
      parts.push(final);
    } else {
      last['isFinal'] = true;
      final = last;
    }
    const elapsed = between(timeline.startedAt, timeline.endedAt);
    if (elapsed !== undefined) final['elapsedMs'] = elapsed;
    // On the final part too: a viewer other than the owner keeps only that part (`sharedCompletion`).
    const cut = cutShortField(answer);
    if (cut.truncatedBy !== undefined) final['truncatedBy'] = cut.truncatedBy;
    if (cut.budgetScope !== undefined) final['budgetScope'] = cut.budgetScope;
    // FU-2: on the final part too, where the saved message and a shared viewer's copy read it.
    const notFound = notFoundField(answer).notFound;
    if (notFound !== undefined) final['notFound'] = notFound;
    const limits = couldNotDoField(answer).limits;
    if (limits !== undefined) final['limits'] = limits;
    // WP-89: on the final part too, so a shared viewer's copy words the outcome as well.
    if (answer.settled !== undefined)
      final['runOutcome'] = { ...answer.settled };
  }
  // Sent while the run ran but never taken (it stopped or ran out of turns): after the answer.
  inputsAfter(null);
  // Main's sign-in card, last (`AnswerFinalizer._attach_parts`); an audience other than the owner keeps only
  // the final text part (`sharedCompletion`).
  if (answer?.mcpSignIn !== undefined) {
    parts.push({ type: 'mcp_sign_in', servers: answer.mcpSignIn });
  }
  return parts;
}

export function toolNames(timeline: Timeline): {
  succeeded_tool_names: string[];
  failed_tool_names: string[];
} {
  return {
    succeeded_tool_names: timeline.outcomes
      .filter((o) => o.ok)
      .map((o) => o.name),
    failed_tool_names: timeline.outcomes
      .filter((o) => !o.ok)
      .map((o) => o.name),
  };
}

/**
 * `completion_data` in each legacy shape (answered, fallback, stopped,
 * error), or `null` when the run ended without an answer (it fails with
 * `RUN_ERROR` and a failure message instead).
 */
export function buildCompletion(
  timeline: Timeline,
): Record<string, unknown> | null {
  const answer = finalAnswer(timeline);
  const parts = buildParts(timeline);
  const names = toolNames(timeline);
  if (answer === null) {
    // A stop by an operator control ends as its run error, which says why (ADR-OPS-01 D5).
    if (
      timeline.end?.status !== 'cancelled' ||
      timeline.stoppedBy !== undefined
    ) {
      return null;
    }
    return { status: 'stopped', parts, answer: '', citations: [], ...names };
  }
  if (askedBeforeRunning(timeline)) {
    // `emit_pre_run_clarification`'s completion: no parts, no tool names;
    // only the messages the user sent meanwhile, which the run never took.
    const inputs = inputParts(timeline);
    return {
      answer: answer.answer,
      citations: answer.citations,
      confidence: answer.confidence,
      ...(answer.answerMatchType !== undefined
        ? { answerMatchType: answer.answerMatchType }
        : {}),
      ...(inputs.length > 0 ? { parts: inputs } : {}),
    };
  }
  const reasoning =
    answer.reasoning !== undefined ? { reasoning: answer.reasoning } : {};
  if (answer.outcome === 'stopped') {
    return {
      status: 'stopped',
      parts,
      answer: answer.answer,
      citations: answer.citations,
      ...reasoning,
      ...names,
    };
  }
  if (
    answer.outcome === 'needs_input' &&
    answer.answer === '' &&
    answer.answerMatchType === undefined
  ) {
    // The agent route's ask_user_question pause (main #3592): the card is the
    // turn, and legacy saves it as an empty `waiting_input` reply.
    return {
      answer: '',
      citations: answer.citations,
      status: 'waiting_input',
      ...names,
      parts,
    };
  }
  if (answer.outcome === 'answered' && answer.answerMatchType === undefined) {
    return {
      parts,
      answer: answer.answer,
      citations: answer.citations,
      confidence: answer.confidence,
      ...reasoning,
      ...names,
      ...cutShortField(answer),
      ...notFoundField(answer),
      ...couldNotDoField(answer),
      ...(answer.settled !== undefined
        ? { runOutcome: { ...answer.settled } }
        : {}),
    };
  }
  return {
    answer: answer.answer,
    citations: answer.citations,
    confidence: answer.confidence,
    ...(answer.answerMatchType !== undefined
      ? { answerMatchType: answer.answerMatchType }
      : {}),
    ...(answer.errorCode !== undefined ? { errorCode: answer.errorCode } : {}),
    ...cutShortField(answer),
    ...names,
    parts,
  };
}

/**
 * An answer a limit cut short (BUDGETEND) says which one, on the completion and the message the web app and the
 * Slack bot show it on; only an answered run is ever cut short.
 */
function cutShortField(
  answer: AnswerRecord,
): Pick<AnswerRecord, 'truncatedBy' | 'budgetScope'> {
  return answer.outcome === 'answered' && answer.truncatedBy !== undefined
    ? {
        truncatedBy: answer.truncatedBy,
        ...(answer.budgetScope !== undefined
          ? { budgetScope: answer.budgetScope }
          : {}),
      }
    : {};
}

/** ADR-RUN-03 D4 (FU-2): an answered run whose subject was not found says so; the web chat words it. */
function notFoundField(answer: AnswerRecord): Pick<AnswerRecord, 'notFound'> {
  return answer.outcome === 'answered' && answer.notFound !== undefined
    ? { notFound: structuredClone(answer.notFound) }
    : {};
}

/** ADR-RUN-03 D5 (R32REPO-FU-1): an answered run that tried and could not do something says so; the web chat words it. */
function couldNotDoField(answer: AnswerRecord): Pick<AnswerRecord, 'limits'> {
  return answer.outcome === 'answered' && answer.limits !== undefined
    ? { limits: structuredClone(answer.limits) }
    : {};
}

/** The run's pre-run asked the user back: the agent never ran (legacy clarification). */
export function askedBeforeRunning(timeline: Timeline): boolean {
  return timeline.turns.length === 0 && timeline.questions.length > 0;
}

/**
 * The `STATE_DELTA` value of a live artifact card (legacy `ArtifactSSEPayload`).
 * The client keys cards by `artifactId`: a card sent again replaces it in place,
 * and `lifecycle: discarded` removes it.
 */
export function artifactCard(artifact: ArtifactView): Record<string, unknown> {
  return {
    artifactId: artifact.artifactId,
    fileName: artifact.fileName,
    mimeType: artifact.mimeType,
    sizeBytes: artifact.sizeBytes,
    // Downloads go through the record; the journal never holds a signed URL.
    downloadUrl: '',
    artifactType: artifact.artifactType,
    isTemporary: artifact.isTemporary,
    ...(artifact.recordId !== undefined ? { recordId: artifact.recordId } : {}),
    ...(artifact.version !== undefined ? { version: artifact.version } : {}),
    ...(artifact.derivedFromCodeArtifactId !== undefined
      ? { derivedFromCodeArtifactId: artifact.derivedFromCodeArtifactId }
      : {}),
    visibility: artifact.visibility,
    ...(artifact.lifecycle !== undefined
      ? { lifecycle: artifact.lifecycle }
      : {}),
  };
}

// Error answers Node writes itself; the web app words their codes (ERR-UX), so they are catalogued answer codes.
const DEADLINE_EXCEEDED = 'deadline_exceeded' satisfies AnswerErrorCode;
const QUARANTINED = 'quarantined';
const DEADLINE_MESSAGE =
  'This run took too long and was stopped. Please try sending your message again.';
/** UX1: the deadline passed while the run waited for a person: nothing was sent, and the user should know why. */
export const APPROVAL_EXPIRED = 'approval_expired' satisfies AnswerErrorCode;
const APPROVAL_EXPIRED_MESSAGE =
  'This task waited for an approval that nobody gave in time, so the action was not taken. Send your message again to try once more.';

/**
 * The run's engine cannot run on this deployment (EH1): its binary is not the
 * allow-listed one, or its image is a candidate build or missing. Only an
 * admin can fix that, so the message never says to try again.
 */
const ENGINE_UNAVAILABLE_MESSAGE =
  "This agent's coding engine can't run on this deployment right now. Nothing was done. Ask your admin.";

/**
 * The run was pinned to an engine image that was rebuilt before it ran (C2.07, C2.20): it never runs on an image it
 * was not admitted under, and a new message pins the new image, so sending again works.
 */
const ENGINE_IMAGE_CHANGED_MESSAGE =
  "This agent's coding engine was updated while this task was waiting, so nothing was done. Send your message again to run it on the updated engine.";

/** Machine names Node sends in `RUN_ERROR.code`, with the user-facing message. */
const FAILURE_MESSAGES: Readonly<Record<string, string>> = {
  [QUARANTINED]:
    'This run kept failing and was stopped. Please try sending your message again.',
  [DEADLINE_EXCEEDED]: DEADLINE_MESSAGE,
  [APPROVAL_EXPIRED]: APPROVAL_EXPIRED_MESSAGE,
  // C4.10 `recovery.autoResume` off: the worker was lost and the run was not resumed from its checkpoint.
  worker_lost:
    'The server running this task stopped, and this agent does not resume on its own, so the task ended. Send your message again to run it once more.',
  capability_lost:
    'A tool or connection this agent needs is no longer available. Please try again.',
  // WP-79: the worker's words name the server (USER_FACING_FAILURES); these stand in when a run carries none.
  mcp_tool_withdrawn:
    'A connected app no longer offers a tool this task was using, so the task stopped. Send your message again to continue without it.',
  // A pinned run whose model the org no longer configures (CPD-6): never continued on another model.
  model_no_longer_configured:
    'The AI model this conversation was using is no longer configured. Send your message again to continue on a current model.',
  // R31GVISO-FU-3: a sub-task's own end names no limit (runFailure names it on the run's own failure).
  out_of_memory:
    "This task ran out of memory and was stopped. Send a smaller task, or ask your admin about the coding engine's memory.",
  // A run stopped by its budget (P6.5): the saved conversation says why (U3).
  budget_exhausted: 'This task reached its spending limit and was stopped.',
  budget_insufficient:
    'This task did not have enough budget left for its next step and was stopped.',
  binary_mismatch: ENGINE_UNAVAILABLE_MESSAGE,
  candidate_image: ENGINE_UNAVAILABLE_MESSAGE,
  engine_unavailable: ENGINE_UNAVAILABLE_MESSAGE,
  engine_image_missing: ENGINE_UNAVAILABLE_MESSAGE,
  // The image was built for another /pipeshub contract than the worker writes (WS-12): an admin rebuilds it.
  engine_contract_unsupported: ENGINE_UNAVAILABLE_MESSAGE,
  // WP103-FU-2: the egress proxy refused a compressed model call it cannot inspect; the engine's setup is the cause.
  model_call_encoded: ENGINE_UNAVAILABLE_MESSAGE,
  // C2.07: the engine image was rebuilt after the run was pinned to the old one; a new message runs on the new one.
  engine_image_changed: ENGINE_IMAGE_CHANGED_MESSAGE,
  // The broker found the sandbox made from another image than the run's pin (C2.20): the same case, caught later.
  image_digest_mismatch: ENGINE_IMAGE_CHANGED_MESSAGE,
  // The engine's floor is gVisor and the host has no runsc: only an admin can fix it.
  gvisor_unavailable: GVISOR_REQUIRED_MESSAGE,
  // ADR-SEC-01: no key this deployment trusts signed the run's image; the cause is in the broker's log.
  image_untrusted:
    "This agent's sandbox image isn't signed by a key this deployment trusts, so nothing was started. Ask your admin.",
  // QA31F: the sandbox broker had no room for the whole bounded wait; capacity, so trying again later can work.
  sandbox_unavailable:
    'No sandbox was free to run this task, so nothing was done. Please try again in a few minutes.',
  // CH-09: the blob or workspace store stayed away for the whole bounded wait; the work up to the last save is kept.
  store_unavailable:
    'File storage could not be reached for several minutes, so this task stopped. Please try again in a few minutes.',
  // SEC-DOS-02: the engine sent events faster than a run may take them for too long; its turn is failed, never retried.
  event_flood:
    'The coding engine sent far more updates than a task can, so it was stopped. Please send your message again.',
  // WP-77: the Model Gateway's codes for a provider that did not answer (a run that met one said only "an internal error").
  provider_unavailable:
    'The AI model provider was unavailable, so this task stopped. Nothing is wrong on your side: please try again in a few minutes.',
  rate_limited:
    'The AI model provider is limiting requests, so this task stopped. Please try again in a few minutes.',
  // SIGNFIX: the coding engine could not reach PipesHub's tools on two attempts; never a crash loop.
  engine_tools_unreachable:
    "The coding engine couldn't reach PipesHub's tools, so this task stopped before it finished. Please send your message again.",
  // C6.12 (WP-61): the run moved more to outside destinations than the deployment's egress cap; the model gateway
  // refused its next call and the engine ended the run with this code.
  egress_volume_exceeded:
    'This task moved more data than one task may on this server, so it stopped. Send a smaller request, or ask your admin about the limit.',
  // WP-103: a model call PipesHub blocked as carrying what looked like a secret, before the task had gathered.
  secret_blocked:
    'PipesHub blocked a request from this task because it looked like it contained a secret (an API key or access token), so the task stopped. Remove the secret from your message or files and send it again.',
};
const DEFAULT_FAILURE =
  'Something went wrong while running the agent. Please try sending your message again.';
/**
 * Failures whose journaled `message` the worker wrote for the user: legacy's
 * own text for a model that cannot start (`chatbot.py`), never a provider's.
 */
const USER_FACING_FAILURES: ReadonlySet<string> = new Set([
  'llm_initialization_failed',
  // A turn scope that cannot be searched (main #4255): the worker refuses with main's words, which name what to
  // do (pick fewer items, wait for the source, ask the agent's owner) and differ for an agent's own limit.
  'selection_too_large',
  'selection_not_ready',
  'selection_empty',
  'accessible_records_not_found',
  // WP-79: a tool its MCP server stopped offering mid-run; the worker's words name the server by its label.
  'mcp_tool_withdrawn',
]);

const markerText = (value: string): string => value.replace(/[[\](){}|]/g, '_');

/**
 * The `::artifact` marker the chat parses from a saved answer (the loop's
 * finalizer writes the same grammar): downloads go through the record.
 */
export function artifactMarker(artifact: ArtifactView): string | null {
  if (artifact.recordId === undefined || artifact.recordId === '') return null;
  const meta = [
    artifact.mimeType,
    artifact.documentId ?? '',
    artifact.recordId,
    artifact.artifactType,
    artifact.version !== undefined ? String(artifact.version) : '',
  ].map(markerText);
  return `::artifact[${markerText(artifact.fileName) || 'artifact'}](record:${markerText(artifact.recordId)}){${meta.join('|')}}`;
}

/**
 * An engine's answer is its final text (E1); its final files reach the saved
 * answer as markers, so they are still there after a reload (U3).
 */
function withEngineArtifacts(
  timeline: Timeline,
  answer: AnswerRecord,
): AnswerRecord {
  if (timeline.engine === null) return answer;
  const markers = timeline.artifacts
    .filter(
      (artifact) =>
        artifact.card &&
        artifact.lifecycle !== 'draft' &&
        artifact.lifecycle !== 'discarded',
    )
    .map(artifactMarker)
    .filter(
      (marker): marker is string =>
        marker !== null && !answer.answer.includes(marker),
    );
  if (markers.length === 0) return answer;
  const suffix = `\n\n${markers.join('\n')}`;
  const partTexts = [...answer.partTexts];
  const last = partTexts.length - 1;
  if (last >= 0) partTexts[last] = `${partTexts[last] ?? ''}${suffix}`;
  return { ...answer, answer: `${answer.answer}${suffix}`, partTexts };
}

/**
 * What a run that ended without an answer keeps beside its failure: the
 * messages the user sent (C-21), and an engine's task, approvals and children,
 * so its card and rows are still there after a reload (U3).
 */
export function failureParts(timeline: Timeline): MessagePart[] {
  const runId = timeline.runId;
  return [
    ...commandParts(timeline),
    ...compactedParts(timeline),
    ...(timeline.engine !== null
      ? [engineTaskPart(timeline.engine, runId)]
      : []),
    ...inputParts(timeline),
    ...[
      ...timeline.approvals.map((view) => ({
        seq: view.seq,
        part: approvalPart(view, runId),
      })),
      ...timeline.connects.map((view) => ({
        seq: view.seq,
        part: connectPart(view, runId),
      })),
      ...timeline.children.map((view) => ({
        seq: view.seq,
        part: childPart(view, runId, childModelLabel(timeline, view)),
      })),
    ]
      .sort((a, b) => a.seq - b.seq)
      .map((entry) => entry.part),
  ];
}

/**
 * The answer a run ends with. A run failed at its absolute deadline (the
 * sweeper ends it; no worker finalized an answer) gets the legacy error
 * answer `AnswerFinalizer` gives a failed model call, so the conversation and
 * the stream show it as one (PA-10).
 */
export function finalAnswer(timeline: Timeline): AnswerRecord | null {
  const end = timeline.end;
  if (timeline.answer !== null)
    return withOutcome(
      timeline,
      withEngineArtifacts(timeline, timeline.answer),
    );
  if (end?.status !== 'failed' || end.errorType !== DEADLINE_EXCEEDED) {
    return null;
  }
  const waited = waitedForApproval(timeline);
  const message = waited ? APPROVAL_EXPIRED_MESSAGE : DEADLINE_MESSAGE;
  // `TranscriptCollector.replace_final_text`: the last text part carries it.
  const partTexts = timeline.turns
    .filter((turn) => turn.synthetic !== true)
    .map((turn) => turn.text);
  partTexts.splice(Math.max(partTexts.length - 1, 0), 1, message);
  return {
    outcome: 'error',
    answer: message,
    partTexts,
    citations: [],
    confidence: 'Low',
    answerMatchType: 'Error',
    errorCode: waited ? APPROVAL_EXPIRED : DEADLINE_EXCEEDED,
  };
}

/** WP-89: an answered run's settled outcome, which the chat words beside the answer's prose. */
function withOutcome(timeline: Timeline, answer: AnswerRecord): AnswerRecord {
  return timeline.outcome !== undefined && answer.outcome === 'answered'
    ? { ...answer, settled: timeline.outcome }
    : answer;
}

/** The run's deadline passed while a person still had to decide on one of its calls. */
function waitedForApproval(timeline: Timeline): boolean {
  return (
    timeline.end?.status === 'failed' &&
    timeline.end.errorType === DEADLINE_EXCEEDED &&
    timeline.approvals.some((view) => view.status === 'expired')
  );
}

/** `out_of_memory` (R31GVISO-FU-3): the code a run ends with when its sandbox's memory limit ended its work. */
export const OUT_OF_MEMORY = 'out_of_memory';

/** A memory limit as a person reads it: whole gigabytes as GB, anything else in MB. */
export function memoryLimitText(mb: number): string {
  return mb % 1024 === 0 ? `${mb / 1024} GB` : `${mb} MB`;
}

function outOfMemoryMessage(mb: number | undefined): string {
  const limit = mb !== undefined ? ` (limit ${memoryLimitText(mb)})` : '';
  return `This task ran out of memory${limit} and was stopped. Send a smaller task, or ask your admin about the coding engine's memory.`;
}

/** The code and message of a run that ended without an answer. */
export function runFailure(timeline: Timeline): {
  code: string;
  message: string;
  servers?: string[];
  /** ADR-OPS-01 D5: the operator control that stopped the run; the web app words its scope. */
  stop?: OperatorStop;
  /** `out_of_memory`: the sandbox's memory limit in MB, which the web app names. */
  memoryLimitMb?: number;
} {
  const end = timeline.end;
  if (end?.status === 'cancelled' && timeline.stoppedBy !== undefined) {
    return operatorStopOutcome(timeline.stoppedBy);
  }
  // WP-84: the labels the reader's words name, beside the worker's English that names them too.
  const servers =
    end?.status === 'failed' && end.servers !== undefined
      ? { servers: end.servers }
      : {};
  // A quarantined run's code is the one the web app words in the reader's
  // language (run-error-codes.json); its cause stays in the journal and the audit.
  const code =
    end?.status === 'quarantined'
      ? QUARANTINED
      : end?.status === 'failed'
        ? end.errorType
        : 'run_failed';
  if (
    end?.status === 'failed' &&
    USER_FACING_FAILURES.has(end.errorType) &&
    end.message !== undefined &&
    end.message.length > 0
  ) {
    return { code, message: end.message, ...servers };
  }
  if (end?.status === 'failed' && end.errorType === OUT_OF_MEMORY) {
    const limit = end.memoryLimitMb;
    return {
      code,
      message: outOfMemoryMessage(limit),
      ...(limit !== undefined ? { memoryLimitMb: limit } : {}),
    };
  }
  if (waitedForApproval(timeline)) {
    return { code: APPROVAL_EXPIRED, message: APPROVAL_EXPIRED_MESSAGE };
  }
  return { code, message: failureMessage(code) ?? DEFAULT_FAILURE, ...servers };
}

let editionFailures: Readonly<Record<string, string>> = Object.freeze({});

/** The run endings an edition adds (`HarnessEdition.runFailureMessages`), with their English; set when the harness is bound. */
export function useRunFailureMessages(
  messages: Readonly<Record<string, string>>,
): void {
  editionFailures = Object.freeze({ ...messages });
}

/** This build's words for a run ending, else an edition's; undefined: neither words it. */
function failureMessage(code: string): string | undefined {
  if (Object.prototype.hasOwnProperty.call(FAILURE_MESSAGES, code))
    return FAILURE_MESSAGES[code];
  return Object.prototype.hasOwnProperty.call(editionFailures, code)
    ? editionFailures[code]
    : undefined;
}

/**
 * A sub-task's ending in words (WP62-ERR): its parent goes on, so these never say the whole message failed. The
 * root-run table words what both share; a code neither names gets the generic line, never the code itself.
 */
const CHILD_FAILURE_MESSAGES: Readonly<Record<string, string>> = {
  cancelled: 'This sub-task was stopped before it finished.',
  // The journal refused what the child tried to save (too large, or a fence it lost): its last save is kept.
  commit_rejected:
    'This sub-task stopped because its work could not be saved. Work saved before that is kept.',
  [DEADLINE_EXCEEDED]: 'This sub-task took too long and was stopped.',
  max_turns_exceeded: 'This sub-task reached its step limit and was stopped.',
  // WP-77: the provider, not PipesHub; the parent goes on with what the sub-task saved.
  provider_unavailable:
    'This sub-task stopped because the AI model provider was unavailable. Work saved before that is kept.',
  rate_limited:
    'This sub-task stopped because the AI model provider was limiting requests. Work saved before that is kept.',
  [QUARANTINED]: 'This sub-task kept failing and was stopped.',
};
export const CHILD_DEFAULT_FAILURE =
  'This sub-task stopped because of an internal error.';

/** The plain message for a sub-task that failed or was cancelled; `errorType` is its machine name, kept apart. */
export function childFailureMessage(
  status: string,
  errorType: string | undefined,
  memoryLimitMb?: number,
): string {
  const code = errorType ?? (status === 'cancelled' ? 'cancelled' : undefined);
  if (code === undefined) return CHILD_DEFAULT_FAILURE;
  if (code === OUT_OF_MEMORY && memoryLimitMb !== undefined)
    return outOfMemoryMessage(memoryLimitMb);
  return (
    CHILD_FAILURE_MESSAGES[code] ??
    failureMessage(code) ??
    CHILD_DEFAULT_FAILURE
  );
}

const MAX_TURNS_EXCEEDED = 'max_turns_exceeded';

/**
 * The run ended because a model call failed: the worker answered with the
 * classified error (`answer.errorCode` is the run's `errorType`). The call
 * itself left no turn in the journal.
 */
export function failedModelCall(timeline: Timeline): boolean {
  const end = timeline.end;
  const answer = timeline.answer;
  return (
    end?.status === 'failed' &&
    answer?.outcome === 'error' &&
    answer.errorCode === end.errorType &&
    end.errorType !== MAX_TURNS_EXCEEDED &&
    end.errorType !== DEADLINE_EXCEEDED
  );
}

/**
 * The run failed before it did anything (its model could not start): legacy
 * answers such a request with `RUN_ERROR` alone, no run frames.
 */
export function failedBeforeStarting(timeline: Timeline): boolean {
  return (
    timeline.end?.status === 'failed' &&
    USER_FACING_FAILURES.has(timeline.end.errorType) &&
    timeline.turns.length === 0 &&
    timeline.goal === undefined &&
    timeline.questions.length === 0 &&
    timeline.answer === null
  );
}

/**
 * What a run leaves in its conversation (P1.8): the same completion the live
 * stream's final snapshot carries, after the questions it asked; or its
 * failure. `null` while the run has not ended.
 */
export function runTranscript(
  events: readonly JournalEvent[],
  runId: string,
): RunTranscript | null {
  const timeline = foldTimeline(events, runId);
  if (timeline.end === null) return null;
  const completion = buildCompletion(timeline);
  if (completion !== null) {
    return {
      kind: 'answer',
      completion,
      questions: timeline.questions
        .filter((question) => question.status === 'success')
        .map((question) => ({ toolData: question.toolData })),
    };
  }
  const inputs = failureParts(timeline);
  return {
    kind: 'failure',
    ...runFailure(timeline),
    ...(inputs.length > 0 ? { parts: inputs } : {}),
  };
}
