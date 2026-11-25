import { isSharedGrant } from '../domain/conversation-grants';
import { createHash } from 'node:crypto';
import { FrameVersion, servesOutcomeFrames } from './frame-version';
import { failedOutcome } from './outcome-view';
import { RETIRED } from '../domain/operator-controls';
import { APPROVAL_COUNTED } from '../domain/interactions';
import type { YieldReason } from '../contracts/generated/core/enums';
import { JournalEvent } from '../domain/journal';
import { frameFor, isQuestionTool } from './audiences';
import { shapedForViewer } from '../domain/token-redaction';
import { ArtifactAccess } from './artifact-access';
import type { ChildRef } from './child-interactions';
import { CitationAccess } from './citation-access';
import {
  AguiFrame,
  AGUI,
  AGUI_CUSTOM,
  Audience,
  LiveDelta,
  ProjectedFrame,
} from './frames';
import {
  abandonFrameId,
  compareStreamPositions,
  deltaFrameId,
  LostMessage,
  lostSuffix,
  journalFrameId,
  OpenAt,
  StreamPosition,
} from './stream-id';
import {
  codePointLength,
  codePoints,
  finalShownText,
  ShownText,
  streamingShownText,
} from './text-filter';
import {
  APPROVAL_PREFIX,
  ApprovalView,
  ArtifactView,
  boundedArgs,
  CallView,
  CHILD_ROLE,
  childModelLabel,
  childStatus,
  ConnectView,
  TimelineBuilder,
  TurnView,
} from './timeline';
import {
  artifactCard,
  askedBeforeRunning,
  buildCompletion,
  childFailureMessage,
  OUT_OF_MEMORY,
  failedBeforeStarting,
  failedModelCall,
  failureFields,
  finalAnswer,
  runFailure,
} from './transcript';
import {
  reconcileVerdict,
  UnknownEffectCard,
  unknownEffectCard,
  UnknownEffectView,
} from './unknown-effects';
import { HeldAsks } from './held-asks';
import { withLineSkills } from './skill-use';

export { HELD_ASK_GRACE_MS } from './held-asks';

/**
 * The AG-UI projection of one run (REPORT 6.2.4, 8): journal events and
 * LiveBus deltas in, the legacy browser frames out, each with an SSE id that
 * names its position so a reconnect resumes exactly after it (L-1, L-13).
 *
 * Deterministic: the same journal and the same deltas give the same frames
 * and ids, which is what makes `Last-Event-ID` resume a silent replay up to
 * the id. Deltas only ever render a message the journal has not closed; a
 * model response always closes its message with the text the worker
 * journaled, so a lost, trimmed or slow delta stream costs liveness, never
 * content (L-2, L-4, L-5).
 */

export interface ProjectorContext {
  runId: string;
  sessionId: string;
  conversationId: string;
  title?: string;
  /** The run's input query: `goal_description` until the run journals its goal. */
  goal: string;
  audience: Audience;
  /**
   * The shared viewer's citation access, read when a frame carries
   * citations (C-6). Absent: every record citation is hidden.
   */
  citations?: () => CitationAccess;
  /** The shared viewer's artifact access (SS0). Absent: every artifact is hidden. */
  artifacts?: () => ArtifactAccess;
  /** A child run's parent (P6.1): its snapshots nest under it. */
  parentRunId?: string;
  /**
   * The run waits in the queue (no worker claimed it yet) as the stream
   * opens: when it was admitted, so the chat can say it is waiting (UX3).
   */
  queuedAt?: string;
  /** The frames' revision the client reads (WP-100); absent: the legacy frames. */
  frames?: FrameVersion;
  /** The clock a held ask's grace is read on (WP115-FU-2); absent: the system's. */
  now?: () => number;
}

export interface ProjectorObserver {
  fenceDropped(fence: number, current: number): void;
  deltaGap(fence: number, attempt: number, expected: number, got: number): void;
}

const silentObserver: ProjectorObserver = {
  fenceDropped: () => undefined,
  deltaGap: () => undefined,
};

interface OpenMessage {
  key: string;
  fence: number;
  attempt: number;
  n: number;
  messageId: string;
  shown: ShownText;
  /** Legacy opens reasoning inside the text message and closes it when text starts. */
  reasoning: 'none' | 'open' | 'closed';
  /** The worker's latest citations for this message's text (CITESTREAM). */
  citations: unknown[];
  /** The answer delta last shown for this message, if the worker numbered its text. */
  answer?: LiveAnswer;
}

interface LiveAnswer {
  citations: unknown[];
  normalizedAnswer: string;
  rawLength: number;
}

export type Terminal =
  { kind: 'finished' } | { kind: 'error'; code: string; message: string };

const hexId = (seed: string, length: number): string =>
  createHash('sha256').update(seed).digest('hex').slice(0, length);

const uuidFrom = (seed: string): string => {
  const hex = hexId(seed, 32);
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
};

const PRESENTATION_OF_RESPONSE = new Set([
  'x.pipeshub.message.presented',
  'x.pipeshub.tool.call_presented',
]);
/** An engine run's progress line (E1), and the frame the chat's `code_task` card reads (U1). */
/** The stream status while a rate-limited model provider's backoff runs (QA17-O3), with `retry_at`. */
export const WAITING_PROVIDER = 'waiting_provider';
const LLM_BACKOFF: YieldReason = 'llm_backoff';
/** The stream status while an engine run waits for the sandbox broker to have room (QA31F), with `retry_at`. */
export const WAITING_SANDBOX = 'waiting_sandbox';
const SANDBOX_WAIT: YieldReason = 'sandbox_wait';
/** The stream status while an engine run waits for its file storage to come back (CH-09), with `retry_at`. */
export const WAITING_STORE = 'waiting_store';
/** A run suspended because its engine version was retired (ADR-OPS-01 D6). */
export const WAITING_ENGINE_VERSION = 'waiting_engine_version';
const STORE_WAIT: YieldReason = 'store_wait';
export const ENGINE_PROGRESS = 'x.pipeshub.engine.progress';
const ENGINE_PROGRESS_FRAME = AGUI_CUSTOM.ENGINE_PROGRESS;
/** `retrieval_context` and `run_usage`, journaled by the worker; owner only (`audiences.ts` admits neither). */
const TELEMETRY_RECORDED = 'x.pipeshub.telemetry.recorded';

export const APPROVAL_REQUESTED_FRAME = AGUI_CUSTOM.APPROVAL_REQUESTED;
export const APPROVAL_RESOLVED_FRAME = AGUI_CUSTOM.APPROVAL_RESOLVED;
export const UNKNOWN_EFFECT_FRAME = AGUI_CUSTOM.UNKNOWN_EFFECT;
/**
 * How many of the run's approvals wait for a person, and whether the run is paused on them (its hold ran out,
 * UXINT-5). It names no tool and no argument, so a shared viewer may read it ("waiting for the owner").
 */
export const INTERACTION_WAITING_FRAME = AGUI_CUSTOM.INTERACTION_WAITING;

/** The status and words a client that cannot show a card reads when the run waits on a person's approval. */
export const AWAITING_APPROVAL_STATUS = 'Waiting';
export const AWAITING_APPROVAL_TEXT =
  "This needs a person's approval before it can go on. Open this conversation in PipesHub to approve or " +
  'decline it; the answer will be saved there.';
/** A call waits for the run's owner to connect an item (G-12), and how that ended. */
export const CONNECT_REQUESTED_FRAME = AGUI_CUSTOM.CONNECT_REQUESTED;
export const CONNECT_RESOLVED_FRAME = AGUI_CUSTOM.CONNECT_RESOLVED;
export const UNKNOWN_EFFECT_RESOLVED_FRAME =
  AGUI_CUSTOM.UNKNOWN_EFFECT_RESOLVED;
/** The run's whole plan after a commit changed it (UXR-B1); owner only. */
export const PLAN_FRAME = AGUI_CUSTOM.PLAN;
/** Something the person should know beside the run's steps (CTL-5b). */
export const NOTICE_FRAME = AGUI_CUSTOM.NOTICE;
/** The /command the run started from (SLASH1): the timeline's first row. */
export const SLASH_COMMAND_FRAME = AGUI_CUSTOM.SLASH_COMMAND;
/** What the person's `/compact` did before the run's first turn (SLASH2 C2), shown as it is journaled. */
export const CONTEXT_COMPACTED_FRAME = AGUI_CUSTOM.CONTEXT_COMPACTED;
/** Legacy's retraction of a model call that failed mid-stream (main #4442, `agui_emitter.py`). */
export const MESSAGE_DISCARDED = AGUI_CUSTOM.MESSAGE_DISCARDED;
const PLAN_EVENTS = new Set(['task.created', 'task.updated']);

const PRESENTATION_OF_OUTCOME = new Set([
  'x.pipeshub.tool.result_presented',
  'x.pipeshub.question.asked',
  'x.pipeshub.tool.unavailable',
  'x.pipeshub.artifact.presented',
]);

/**
 * An engine progress line's frame. `positioned`: with its turn and sequence, for a child's line said on its
 * parent's stream, which a resumed stream says again (its run's own lines have a journal position instead).
 */
export function engineProgressFrame(
  data: Record<string, unknown>,
  runId: string,
  positioned = false,
): AguiFrame {
  return {
    type: AGUI.CUSTOM,
    name: ENGINE_PROGRESS_FRAME,
    value: {
      tool: data['tool'],
      taskId: data['taskId'],
      kind: data['kind'],
      text: data['text'],
      data: withLineSkills(
        typeof data['kind'] === 'string' ? data['kind'] : '',
        typeof data['text'] === 'string' ? data['text'] : '',
        (data['data'] ?? {}) as Record<string, unknown>,
      ),
      ...(positioned && typeof data['turnId'] === 'string'
        ? { turnId: data['turnId'] }
        : {}),
      ...(positioned && typeof data['seq'] === 'number'
        ? { seq: data['seq'] }
        : {}),
    },
    runId,
  };
}

/** A visible call's start, as legacy's emitter announced it. */
export function callStartFrames(call: CallView, runId: string): AguiFrame[] {
  return [
    {
      type: AGUI.TOOL_CALL_START,
      toolCallId: call.callId,
      toolCallName: call.name,
      runId,
      ...(call.displayName !== undefined
        ? { displayName: call.displayName }
        : {}),
    },
    {
      type: AGUI.TOOL_CALL_ARGS,
      toolCallId: call.callId,
      delta: call.argsJson,
      runId,
      argsSummary: call.argsSummary ?? null,
      argsPreview: boundedArgs(call),
      ...(call.step !== undefined ? { step: { ...call.step } } : {}),
    },
  ];
}

/** A visible call's end and result. */
export function callResultFrames(call: CallView, runId: string): AguiFrame[] {
  const blocked = call.status === 'blocked';
  return [
    { type: AGUI.TOOL_CALL_END, toolCallId: call.callId, runId },
    {
      type: AGUI.TOOL_CALL_RESULT,
      toolCallId: call.callId,
      content: call.content ?? '',
      role: 'tool',
      status: call.status ?? 'failed',
      runId,
      resultSummary: blocked ? null : (call.resultSummary ?? null),
      ...failureFields(call),
      ...(call.status === 'completed' && call.resultView !== undefined
        ? { resultView: call.resultView }
        : {}),
      ...(call.step !== undefined ? { step: { ...call.step } } : {}),
    },
  ];
}

export class RunProjector {
  private readonly timeline: TimelineBuilder;
  private position: StreamPosition = { seq: 0, k: 0, n: 0, m: 0 };
  /** The message streaming when the current journal event began (journal frame ids). */
  private before: OpenAt | null = null;
  private open: OpenMessage | null = null;
  private journalFence = 0;
  private started = false;
  private startedAt = '';
  private toolTurns = 0;
  private llmSnapshotAt = -1;
  /** The run yielded for the model provider's backoff and has not been claimed again. */
  private waitingForProvider = false;
  private attemptOfRequest = 0;
  private readonly closed = new Set<string>();
  /** Messages whose "still calling the model" snapshot on tool arguments went out. */
  private readonly announced = new Set<string>();
  private readonly lost = new Set<string>();
  private readonly held: LiveDelta[] = [];
  private readonly finishedTurns = new Set<number>();
  /** Artifacts the client holds a card for: a later presentation updates that card (U1). */
  private readonly shownCards = new Set<string>();
  /** An engine run (E1): its progress lines seen. */
  private engineRun = false;
  /**
   * The message the client had that the projector ended unfinished: the next
   * `TEXT_MESSAGE_START` replaces it (`replacesMessageId`). One abandoned at a
   * resume cursor is only replaced by a start the client receives, so the
   * silent replay of the journal before the cursor does not consume it.
   */
  private replacing: {
    messageId: string;
    afterCursor: boolean;
    fence: number;
    attempt: number;
  } | null = null;
  private terminalState: Terminal | null = null;
  private terminalSeq = 0;
  /** The child run most recently spawned and not yet ended (P6.1). */
  private currentChild: string | null = null;
  /** The run suspended on a person's decision and has not been claimed since (UXINT-5). */
  private paused = false;
  /** Each invocation's last reconcile verdict (P2.9), for its unknown-effect card. */
  private readonly verdicts = new Map<
    string,
    NonNullable<UnknownEffectView['reconcile']>
  >();
  /** Unknown effects whose card went out: only those get a resolution frame. */
  private readonly unknownShown = new Set<string>();
  /** Asks a grant the run holds answers: drawn with their answer, not before (WP115-FU-2). */
  private readonly heldAsks: HeldAsks;

  constructor(
    private readonly context: ProjectorContext,
    private readonly resumeAfter: StreamPosition | null = null,
    private readonly observer: ProjectorObserver = silentObserver,
  ) {
    this.timeline = new TimelineBuilder(context.runId);
    this.heldAsks = new HeldAsks(context.now);
  }

  /** When a held ask's card is next due (`releaseHeld`); null when none is held. */
  heldUntil(): number | null {
    return this.heldAsks.until();
  }

  /** The cards of held asks Node did not answer within the grace: the person decides them now. */
  releaseHeld(now: number): ProjectedFrame[] {
    const out: ProjectedFrame[] = [];
    const due = this.heldAsks.due(now);
    for (const interactionId of due) this.approvalCard(interactionId, out);
    if (due.length > 0) this.waiting(out);
    return out;
  }

  get terminal(): Terminal | null {
    return this.terminalState;
  }

  /** A card of the run waits on a person's decision, and the run has not ended. */
  get awaitingApproval(): boolean {
    if (this.terminalState !== null) return false;
    const pending = this.timeline.timeline.approvals.filter(
      (a) => a.status === 'pending',
    ).length;
    return pending - this.heldAsks.count > 0;
  }

  /**
   * R34-FIX D-MCP-ASK: an audience that cannot show a card (an MCP client's `pipeshub_chat`) is handed the
   * conversation as it stands, with the words a person reads, and its stream ends; the run stays parked and the
   * person decides in PipesHub, where the answer is saved. `RUN_FINISHED` with a `result` is the frame that client's
   * reader ends on. D-SLACK-ASK: its `meta` names what the run waits on and the conversation's link, which the Slack
   * bot puts in the thread.
   */
  handOff(
    conversation: Record<string, unknown>,
    meta: Record<string, unknown>,
  ): ProjectedFrame[] {
    const out: ProjectedFrame[] = [];
    const messages = Array.isArray(conversation['messages'])
      ? (conversation['messages'] as unknown[])
      : [];
    this.emit(out, 'j', {
      type: AGUI.RUN_FINISHED,
      result: {
        conversation: {
          ...conversation,
          status: AWAITING_APPROVAL_STATUS,
          messages: [
            ...messages,
            { messageType: 'bot_response', content: AWAITING_APPROVAL_TEXT },
          ],
        },
        meta: {
          ...meta,
          awaiting: 'approval',
          waitingOn: this.awaitedActions(),
        },
      },
    });
    return out;
  }

  /** The calls the run waits on (each tool once, in the order asked), named as the run's tool frames name them. */
  private awaitedActions(): Array<{ toolName: string; displayName?: string }> {
    const out: Array<{ toolName: string; displayName?: string }> = [];
    for (const approval of this.timeline.timeline.approvals) {
      if (
        approval.status !== 'pending' ||
        this.heldAsks.holds(approval.interactionId)
      ) {
        continue;
      }
      if (out.some((a) => a.toolName === approval.toolName)) continue;
      out.push({
        toolName: approval.toolName,
        ...(approval.displayName !== undefined
          ? { displayName: approval.displayName }
          : {}),
      });
    }
    return out;
  }

  /** The run's children (P6.1): the owner's chat follows the interactions of the live ones (UX3). */
  children(): ChildRef[] {
    return this.timeline.timeline.children.map((child) => ({
      childRunId: child.runId,
      role: child.role.startsWith(`${CHILD_ROLE}-`)
        ? child.role.slice(CHILD_ROLE.length + 1)
        : child.role,
      spawnSeq: child.seq,
      live: child.status === 'running',
    }));
  }

  get journalSeq(): number {
    return this.position.seq;
  }

  get openMessage(): { fence: number; attempt: number; n: number } | null {
    return this.open === null
      ? null
      : { fence: this.open.fence, attempt: this.open.attempt, n: this.open.n };
  }

  // ---- frames and positions ----

  private emit(
    out: ProjectedFrame[],
    kind: 'j' | 'd',
    frame: AguiFrame,
    delta: { fence: number; attempt: number } | null = null,
  ): void {
    let id: string;
    let order: StreamPosition;
    if (kind === 'j') {
      this.position = { ...this.position, k: this.position.k + 1, n: 0, m: 0 };
      order = this.position;
      id = journalFrameId(this.position.seq, this.position.k, this.before);
    } else {
      this.position = { ...this.position, m: this.position.m + 1 };
      order = this.position;
      id = deltaFrameId(this.position, delta?.fence ?? 0, delta?.attempt ?? 0);
    }
    const silent =
      this.resumeAfter !== null &&
      compareStreamPositions(order, this.resumeAfter) <= 0;
    const marked =
      frame.type === AGUI.TEXT_MESSAGE_START
        ? this.markRestart(frame, silent)
        : frame;
    if (silent) return;
    const visible = this.forAudience(marked);
    if (visible === null) return;
    out.push({ id: id + this.lostSuffix(), frame: visible });
  }

  private lostSuffix(): string {
    const pending =
      this.replacing === null
        ? null
        : `${this.replacing.fence}.${this.replacing.attempt}`;
    return lostSuffix(
      [...this.lost].map((key) => {
        const [fence, attempt] = key.split('.').map(Number) as [number, number];
        return { fence, attempt, pending: key === pending };
      }),
    );
  }

  /**
   * A resume: the messages the client had ended unfinished (named by its
   * `Last-Event-ID`) restart under new ids, and a restart it has not seen yet
   * still says what it replaces.
   */
  restoreLost(messages: readonly LostMessage[]): void {
    for (const message of messages) {
      const key = `${message.fence}.${message.attempt}`;
      this.lost.add(key);
      if (message.pending) {
        this.replacing = {
          messageId: this.messageId(key, false),
          afterCursor: true,
          fence: message.fence,
          attempt: message.attempt,
        };
      }
    }
  }

  private markRestart(frame: AguiFrame, silent: boolean): AguiFrame {
    const replacing = this.replacing;
    if (replacing === null || (replacing.afterCursor && silent)) return frame;
    this.replacing = null;
    return { ...frame, replacesMessageId: replacing.messageId };
  }

  /** Delta frames of the message being consumed carry its identity. */
  private emitDelta(
    out: ProjectedFrame[],
    frame: AguiFrame,
    fence: number,
    attempt: number,
  ): void {
    this.emit(out, 'd', frame, { fence, attempt });
  }

  private forAudience(frame: AguiFrame): AguiFrame | null {
    const audience = this.context.audience;
    if (audience === 'owner') return frame;
    const shown = frameFor(audience, frame, {
      checkedCitations: () =>
        this.context.citations?.() ?? CitationAccess.denyRecords(),
      checkedArtifacts: () =>
        this.context.artifacts?.() ?? ArtifactAccess.hideAll(),
      asksUser: (toolCallId) => {
        const call =
          typeof toolCallId === 'string'
            ? this.timeline.call(toolCallId)
            : undefined;
        return call !== undefined && isQuestionTool(call.name);
      },
    });
    // A participant who is not the run's actor gets the bytes masked, not only the render (SEC4).
    return shown !== null && audience === 'shared'
      ? (shapedForViewer(shown) as AguiFrame)
      : shown;
  }

  /**
   * Legacy first frame: Node's `conversation_created`, before anything the
   * run did. The owner's names the run too, so a client that sent one
   * request on a chat route can stop it and follow it after a dropped
   * connection (LAT1).
   */
  begin(): ProjectedFrame[] {
    const out: ProjectedFrame[] = [];
    this.emit(out, 'j', {
      type: AGUI.CUSTOM,
      name: AGUI_CUSTOM.CONVERSATION_CREATED,
      value: {
        conversationId: this.context.conversationId,
        ...(this.context.title !== undefined
          ? { title: this.context.title }
          : {}),
        ...(this.context.audience === 'owner'
          ? { runId: this.context.runId }
          : {}),
      },
    });
    if (this.context.queuedAt !== undefined) {
      this.emit(out, 'j', this.runState('queued', this.context.queuedAt));
    }
    return out;
  }

  // ---- the journal ----

  /** Events of the session stream in seq order; the run's own are projected. */
  project(events: readonly JournalEvent[]): ProjectedFrame[] {
    const out: ProjectedFrame[] = [];
    for (let index = 0; index < events.length; index += 1) {
      const event = events[index] as JournalEvent;
      if (event.seq <= this.position.seq) continue;
      this.position = { seq: event.seq, k: 0, n: 0, m: 0 };
      this.before = this.openMessage;
      if (event.runId !== this.context.runId) continue;
      this.heldAsks.apply(event);
      const last = this.projectEvent(event, events, index, out);
      if (last > index) {
        // Presentation written with the event is part of it: later frames follow it.
        this.position = {
          ...this.position,
          seq: (events[last] as JournalEvent).seq,
        };
        index = last;
      }
    }
    return out;
  }

  private projectEvent(
    event: JournalEvent,
    events: readonly JournalEvent[],
    index: number,
    out: ProjectedFrame[],
  ): number {
    const data = event.data ?? {};
    switch (event.type) {
      case 'run.suspended':
        this.timeline.apply(event);
        if (data['reason'] === 'approval' && this.openApprovals() > 0) {
          this.paused = true;
          this.waiting(out);
        }
        // ADR-OPS-01 D6: held until its engine version is allowed again; its notice says so.
        if (data['reason'] === RETIRED) {
          this.emit(out, 'j', this.runState(WAITING_ENGINE_VERSION, event.ts));
        }
        return index;
      case 'run.claimed':
        this.timeline.apply(event);
        if (this.paused) {
          this.paused = false;
          this.waiting(out);
        }
        this.claimed(Number(data['fence']) || event.fence || 0, event.ts, out);
        if (this.waitingForProvider) {
          // Re-claimed after the provider's wait: the model is called again.
          this.waitingForProvider = false;
          this.llmSnapshotAt = -1;
          this.llmSnapshot(event.ts, out);
        }
        return index;
      case 'run.yielded':
        this.noticed(event, out);
        if (data['reason'] === LLM_BACKOFF) this.retryWait(data, event.ts, out);
        else if (data['reason'] === SANDBOX_WAIT)
          this.retryWait(data, event.ts, out, WAITING_SANDBOX);
        else if (data['reason'] === STORE_WAIT)
          this.retryWait(data, event.ts, out, WAITING_STORE);
        return index;
      case 'x.pipeshub.goal.presented':
        this.timeline.apply(event);
        this.start(event.ts, out);
        return index;
      case 'turn.started':
        this.timeline.apply(event);
        this.start(event.ts, out);
        this.llmSnapshot(event.ts, out);
        return index;
      case 'model.request':
        this.timeline.apply(event);
        this.attemptOfRequest =
          Number(data['modelAttempt']) || this.attemptOfRequest;
        return index;
      case 'model.response': {
        this.timeline.apply(event);
        const last = this.absorb(events, index, PRESENTATION_OF_RESPONSE);
        this.response(event, out);
        return last;
      }
      case 'tool.outcome': {
        this.timeline.apply(event);
        const call = this.timeline.callForInvocation(data['invocationId']);
        const last = this.absorb(events, index, PRESENTATION_OF_OUTCOME);
        if (call !== undefined) this.outcome(call, event.ts, out);
        return last;
      }
      case 'input.consumed': {
        this.timeline.apply(event);
        const input = this.timeline.input(data['messageSeq']);
        if (input !== undefined) {
          this.emit(out, 'j', {
            type: AGUI.CUSTOM,
            name: AGUI_CUSTOM.USER_INPUT,
            value: {
              messageSeq: input.messageSeq,
              content: input.text,
              ...(input.attachments !== undefined
                ? { attachments: input.attachments }
                : {}),
            },
            runId: this.context.runId,
          });
          // The model is called again on what the user said: say so, as for a tool turn.
          this.llmSnapshotAt = -1;
        }
        return index;
      }
      case 'interaction.requested':
        this.timeline.apply(event);
        this.approvalRequested(data, event.ts, out);
        this.connectRequested(data, out);
        this.unknownEffect(data, out);
        return index;
      case 'interaction.resolved':
        this.timeline.apply(event);
        this.approvalResolved(data, out);
        this.connectResolved(data, out);
        this.unknownEffectResolved(data, out);
        return index;
      case 'interaction.granted':
        this.timeline.apply(event);
        this.approvalGranted(event.writer, data, out);
        return index;
      case APPROVAL_COUNTED:
        this.timeline.apply(event);
        if (event.writer === 'node') this.approvalCounted(data, out);
        return index;
      case 'tool.reconciled': {
        this.timeline.apply(event);
        const found = reconcileVerdict(data);
        if (found !== null)
          this.verdicts.set(found.invocationId, found.verdict);
        return index;
      }
      case 'run.completed':
      case 'run.cancelled':
      case 'run.failed':
      case 'run.quarantined':
        this.timeline.apply(event);
        this.end(event.ts, event.seq, out);
        return index;
      case 'child.spawned':
      case 'child.completed':
        this.timeline.apply(event);
        this.child(event, out);
        return index;
      case 'x.pipeshub.command.expanded':
        this.timeline.apply(event);
        return index;
      case 'x.pipeshub.context.compacted': {
        this.timeline.apply(event);
        const compacted = this.timeline.timeline.compacted;
        if (compacted !== undefined) {
          this.emit(out, 'j', {
            type: AGUI.CUSTOM,
            name: CONTEXT_COMPACTED_FRAME,
            value: compacted,
            runId: this.context.runId,
          });
        }
        return index;
      }
      // G-05: the run's verification reads as a notice beside its answer, live as on reload.
      // G-48: a move to an alternate model reads as a notice too.
      case 'verification.recorded':
      case 'model.fallback':
      case 'x.pipeshub.run.notice':
        this.noticed(event, out);
        return index;
      case 'x.pipeshub.artifact.presented': {
        this.timeline.apply(event);
        const shown = this.timeline.artifact(data['artifactId']);
        // An engine's outputs belong to no tool call: its finals are cards as
        // they are presented, its drafts rows of its task card (E1).
        const engineFinal = this.engineRun && shown?.lifecycle === 'final';
        if (
          shown !== undefined &&
          (this.shownCards.has(shown.artifactId) || engineFinal)
        ) {
          this.artifactFrame(shown, out);
        }
        return index;
      }
      case TELEMETRY_RECORDED:
        // A run admitted with `includeRetrievalContext` (SYNC2-F1): today's loop's measurement frames, as journaled.
        this.timeline.apply(event);
        this.start(event.ts, out);
        this.emit(out, 'j', {
          type: AGUI.CUSTOM,
          name: data['name'],
          value: data['value'],
          runId: this.context.runId,
        });
        return index;
      case ENGINE_PROGRESS:
        this.timeline.apply(event);
        this.engineProgress(event, data, out);
        return index;
      case 'task.created':
      case 'task.updated': {
        this.timeline.apply(event);
        // One frame for a run of steps, at its last: its id is the same however the journal was paged, so a
        // resume after any id this stream sent still receives the final plan.
        const next = events[index + 1];
        if (
          next !== undefined &&
          next.runId === event.runId &&
          PLAN_EVENTS.has(next.type)
        )
          return index;
        this.start(event.ts, out);
        this.plan(out);
        return index;
      }
      default:
        this.timeline.apply(event);
        return index;
    }
  }

  /** Folds the presentation events a commit wrote after `index`; returns the last one. */
  private absorb(
    events: readonly JournalEvent[],
    index: number,
    kinds: ReadonlySet<string>,
  ): number {
    let last = index;
    while (last + 1 < events.length) {
      const next = events[last + 1] as JournalEvent;
      if (next.runId !== this.context.runId || !kinds.has(next.type)) break;
      this.timeline.apply(next);
      last += 1;
    }
    return last;
  }

  private claimed(fence: number, ts: string, out: ProjectedFrame[]): void {
    if (fence > this.journalFence) {
      if (this.open !== null && this.open.fence < fence)
        this.closeOpen(out, null);
      this.journalFence = fence;
    }
    if (this.startedAt === '') this.startedAt = ts;
    const held = this.held
      .splice(0)
      .filter((d) => d.fence <= this.journalFence);
    for (const delta of held) this.consume(delta, out);
  }

  /**
   * Legacy starts the run once its pre-run steps have settled the goal, so
   * `RUN_STARTED` follows the journaled goal (or whatever the run does first).
   */
  private start(
    ts: string,
    out: ProjectedFrame[],
    delta: { fence: number; attempt: number } | null = null,
  ): void {
    if (this.started) return;
    this.started = true;
    if (this.startedAt === '') this.startedAt = ts;
    const kind = delta === null ? 'j' : 'd';
    this.emit(
      out,
      kind,
      {
        type: AGUI.RUN_STARTED,
        threadId: this.context.sessionId,
        runId: this.context.runId,
      },
      delta,
    );
    this.emit(out, kind, this.runState('starting', ts), delta);
    const command = this.timeline.timeline.command;
    if (command !== undefined) {
      this.emit(
        out,
        kind,
        {
          type: AGUI.CUSTOM,
          name: SLASH_COMMAND_FRAME,
          value: command,
          runId: this.context.runId,
        },
        delta,
      );
    }
    // Journaled with run.admitted, before the run started: shown once it has (CTL-5b).
    for (const notice of this.timeline.timeline.notices) {
      this.emit(
        out,
        kind,
        {
          type: AGUI.CUSTOM,
          name: NOTICE_FRAME,
          value: notice,
          runId: this.context.runId,
        },
        delta,
      );
    }
  }

  /**
   * A rate-limited model provider (QA17-O3): the run waits until `notBefore` and retries; the person reads that
   * instead of a "Thinking..." that does not move.
   */
  private retryWait(
    data: Record<string, unknown>,
    ts: string,
    out: ProjectedFrame[],
    status: string = WAITING_PROVIDER,
  ): void {
    const state = this.runState(status, ts);
    const notBefore = data['notBefore'];
    this.emit(out, 'j', {
      ...state,
      snapshot: {
        ...(state['snapshot'] as Record<string, unknown>),
        retry_at: typeof notBefore === 'string' ? notBefore : null,
      },
    });
    this.waitingForProvider = true;
  }

  private llmSnapshot(
    ts: string,
    out: ProjectedFrame[],
    delta: { fence: number; attempt: number } | null = null,
  ): void {
    if (this.llmSnapshotAt === this.toolTurns) return;
    this.llmSnapshotAt = this.toolTurns;
    this.emit(
      out,
      delta === null ? 'j' : 'd',
      this.runState('calling_llm', ts),
      delta,
    );
  }

  private response(event: JournalEvent, out: ProjectedFrame[]): void {
    const turn = this.timeline.currentTurn;
    if (turn === undefined) return;
    this.start(event.ts, out);
    this.llmSnapshot(event.ts, out);
    const key = `${turn.fence}.${turn.modelAttempt}`;
    const open = this.open;
    const shownAnswer = open?.key === key ? open.answer : undefined;
    if (open !== null && open.key === key) {
      if (open.reasoning === 'open') this.closeReasoning(out, 'j', open, null);
      const rest = open.shown.finish(turn.text);
      if (rest.length > 0) {
        this.emit(out, 'j', this.content(open.messageId, rest));
      }
      // The deltas that would have announced the arguments never arrived.
      if (callsTools(turn)) this.announce(key, event.ts, out, null);
      this.open = null;
      this.emit(out, 'j', this.textEnd(open.messageId));
    } else {
      if (open !== null) this.closeOpen(out, null);
      const messageId = this.messageId(key, this.lost.has(key));
      this.emit(out, 'j', {
        type: AGUI.TEXT_MESSAGE_START,
        messageId,
        role: 'assistant',
        runId: this.context.runId,
      });
      if (turn.reasoning !== undefined) {
        const reasoningId = this.reasoningId(key, this.lost.has(key));
        for (const frame of this.reasoningFrames(reasoningId, turn.reasoning)) {
          this.emit(out, 'j', frame);
        }
      }
      // A provider streams the text, then each call's arguments in call
      // order: legacy announces the first call that is not the answer.
      const { head, answer, before } = splitAnswer(turn);
      const shown = finalShownText(head);
      if (shown.length > 0) this.emit(out, 'j', this.content(messageId, shown));
      if (before) this.announce(key, event.ts, out, null);
      const answered = finalShownText(answer);
      if (answered.length > 0) {
        this.emit(out, 'j', this.content(messageId, answered));
      }
      if (callsTools(turn)) this.announce(key, event.ts, out, null);
      this.emit(out, 'j', this.textEnd(messageId));
    }
    this.closed.add(key);
    const visible = turn.calls.filter((call) => !call.hidden);
    if (turn.calls.length === 0) {
      this.emit(out, 'j', {
        type: AGUI.STATE_DELTA,
        runId: this.context.runId,
        delta: answerDelta({
          citations: shownAnswer?.citations ?? [],
          normalizedAnswer: closedAnswer(turn.text, shownAnswer),
          rawLength: codePointLength(turn.text),
        }),
      });
    }
    for (const call of visible) {
      for (const frame of callStartFrames(call, this.context.runId)) {
        this.emit(out, 'j', frame);
      }
      this.emit(out, 'j', this.runState('running_tool', event.ts, call.name));
    }
    if (turn.calls.length > 0 && visible.length === 0) this.turnDone(turn.turn);
  }

  private artifactFrame(artifact: ArtifactView, out: ProjectedFrame[]): void {
    if (artifact.lifecycle === 'discarded') {
      this.shownCards.delete(artifact.artifactId);
    } else {
      this.shownCards.add(artifact.artifactId);
    }
    // Legacy oddity 3: a live artifact frame names the conversation as its run.
    this.emit(out, 'j', {
      type: AGUI.STATE_DELTA,
      runId: this.context.sessionId,
      delta: [
        { op: 'add', path: '/artifacts/-', value: artifactCard(artifact) },
      ],
    });
  }

  private outcome(call: CallView, _ts: string, out: ProjectedFrame[]): void {
    if (!call.hidden) {
      const snapshot = this.timeline.timeline;
      for (const question of snapshot.questions) {
        if (question.callId !== call.callId) continue;
        this.emit(out, 'j', {
          type: AGUI.CUSTOM,
          name: AGUI_CUSTOM.ASK_USER_QUESTION,
          value: { status: question.status, toolData: question.toolData },
          runId: this.context.runId,
        });
      }
      for (const found of snapshot.unavailable) {
        if (found.callId !== call.callId) continue;
        for (const hit of found.hits) {
          this.emit(out, 'j', {
            type: AGUI.CUSTOM,
            name: AGUI_CUSTOM.TOOL_UNAVAILABLE,
            value: hit,
            runId: this.context.runId,
          });
        }
      }
      for (const artifact of snapshot.artifacts) {
        if (artifact.callId !== call.callId || !artifact.card) continue;
        if (artifact.lifecycle === 'discarded') continue;
        this.artifactFrame(artifact, out);
      }
      for (const frame of callResultFrames(call, this.context.runId)) {
        this.emit(out, 'j', frame);
      }
    }
    const turn = this.timeline.timeline.turns.find((t) => t.turn === call.turn);
    if (
      turn !== undefined &&
      turn.calls.every((c) => c.outcome !== undefined)
    ) {
      this.turnDone(turn.turn);
    }
  }

  /**
   * A call held for a person's approval (P2.6c): the card's content, from the
   * journaled call, never from model markup. Only the owner's stream admits it.
   */
  private approvalRequested(
    data: Record<string, unknown>,
    ts: string,
    out: ProjectedFrame[],
  ): void {
    const interactionId = data['interactionId'];
    if (
      data['kind'] !== 'approval' ||
      typeof interactionId !== 'string' ||
      !interactionId.startsWith(APPROVAL_PREFIX)
    ) {
      return;
    }
    if (this.timeline.approval(interactionId) === undefined) return;
    if (this.heldAsks.hold(interactionId, ts)) return;
    this.approvalCard(interactionId, out);
    this.waiting(out);
  }

  private approvalCard(interactionId: string, out: ProjectedFrame[]): void {
    const view = this.timeline.approval(interactionId);
    if (view === undefined) return;
    this.emit(out, 'j', {
      type: AGUI.CUSTOM,
      name: APPROVAL_REQUESTED_FRAME,
      value: approvalCard(view),
      runId: this.context.runId,
    });
  }

  /** A held ask's card goes out just before its answer, so the client never holds it open. */
  private heldCard(interactionId: unknown, out: ProjectedFrame[]): void {
    if (
      typeof interactionId === 'string' &&
      this.heldAsks.release(interactionId)
    ) {
      this.approvalCard(interactionId, out);
    }
  }

  private openApprovals(): number {
    const { approvals, connects } = this.timeline.timeline;
    return (
      approvals.filter((a) => a.status === 'pending').length -
      this.heldAsks.count +
      connects.filter((c) => c.status === 'pending').length
    );
  }

  private connectRequested(
    data: Record<string, unknown>,
    out: ProjectedFrame[],
  ): void {
    const view = this.timeline.connect(data['interactionId']);
    if (data['kind'] !== 'connect' || view === undefined) return;
    this.emit(out, 'j', {
      type: AGUI.CUSTOM,
      name: CONNECT_REQUESTED_FRAME,
      value: connectCard(view),
      runId: this.context.runId,
    });
    this.waiting(out);
  }

  private connectResolved(
    data: Record<string, unknown>,
    out: ProjectedFrame[],
  ): void {
    const view = this.timeline.connect(data['interactionId']);
    if (view === undefined) return;
    this.emit(out, 'j', {
      type: AGUI.CUSTOM,
      name: CONNECT_RESOLVED_FRAME,
      value: { interactionId: view.interactionId, status: view.status },
      runId: this.context.runId,
    });
    this.waiting(out);
  }

  private waiting(out: ProjectedFrame[]): void {
    this.emit(out, 'j', {
      type: AGUI.CUSTOM,
      name: INTERACTION_WAITING_FRAME,
      value: { count: this.openApprovals(), paused: this.paused },
      runId: this.context.runId,
    });
  }

  /**
   * A call whose outcome nobody knows (P2.9) holds the run for a person: the
   * owner's chat says so and offers the decision (UX3), as for an approval.
   */
  private unknownEffect(
    data: Record<string, unknown>,
    out: ProjectedFrame[],
  ): void {
    const card: UnknownEffectCard | null = unknownEffectCard(
      this.timeline,
      data['interactionId'],
      this.verdicts,
    );
    if (card === null) return;
    this.unknownShown.add(card.interactionId);
    this.emit(out, 'j', {
      type: AGUI.CUSTOM,
      name: UNKNOWN_EFFECT_FRAME,
      value: card,
      runId: this.context.runId,
    });
  }

  private unknownEffectResolved(
    data: Record<string, unknown>,
    out: ProjectedFrame[],
  ): void {
    const interactionId = data['interactionId'];
    if (
      typeof interactionId !== 'string' ||
      !this.unknownShown.has(interactionId)
    ) {
      return;
    }
    this.emit(out, 'j', {
      type: AGUI.CUSTOM,
      name: UNKNOWN_EFFECT_RESOLVED_FRAME,
      value: { interactionId, decision: data['decision'] },
      runId: this.context.runId,
    });
  }

  /** An engine run's progress line as the `code_task` card reads it, on either stream (U1). */
  private engineProgress(
    event: JournalEvent,
    data: Record<string, unknown>,
    out: ProjectedFrame[],
  ): void {
    this.engineRun = true;
    this.start(event.ts, out);
    this.emit(out, 'j', engineProgressFrame(data, this.context.runId));
  }

  /** A snapshot, never a delta: a resumed or second client needs no fold of its own. */
  private plan(out: ProjectedFrame[]): void {
    const { tasks, currentTaskId } = this.timeline.timeline;
    this.emit(out, 'j', {
      type: AGUI.CUSTOM,
      name: PLAN_FRAME,
      value: {
        runId: this.context.runId,
        steps: tasks.map((task) => ({ ...task, deps: [...task.deps] })),
        currentTaskId,
      },
      runId: this.context.runId,
    });
  }

  private approvalResolved(
    data: Record<string, unknown>,
    out: ProjectedFrame[],
  ): void {
    const interactionId = data['interactionId'];
    if (this.timeline.approval(interactionId) === undefined) return;
    this.heldCard(interactionId, out);
    this.emit(out, 'j', {
      type: AGUI.CUSTOM,
      name: APPROVAL_RESOLVED_FRAME,
      value: { interactionId, decision: data['decision'] },
      runId: this.context.runId,
    });
    this.waiting(out);
  }

  /**
   * The approval was for the rest of the conversation (UX1): the card's receipt says so live, as after a reload. A
   * grant answers asks the person never saw (FLOWGRANT, Node answers each open one it covers), so this is how they
   * leave the list as allowed for the conversation, not once.
   */
  private approvalGranted(
    writer: string,
    data: Record<string, unknown>,
    out: ProjectedFrame[],
  ): void {
    const interactionId = data['interactionId'];
    if (
      writer !== 'node' ||
      data['scope'] !== 'conversation' ||
      isSharedGrant(data)
    ) {
      return;
    }
    if (this.timeline.approval(interactionId) === undefined) return;
    this.heldCard(interactionId, out);
    this.emit(out, 'j', {
      type: AGUI.CUSTOM,
      name: APPROVAL_RESOLVED_FRAME,
      value: { interactionId, decision: 'approved', scope: 'conversation' },
      runId: this.context.runId,
    });
  }

  /**
   * WP-100: one more person approved an ask that needs several and still waits; the card's count moves live. Counts
   * only, never who; the timeline's fold is the one count.
   */
  private approvalCounted(
    data: Record<string, unknown>,
    out: ProjectedFrame[],
  ): void {
    const view = this.timeline.approval(data['interactionId']);
    if (view?.approvals === undefined || view.status !== 'pending') return;
    this.emit(out, 'j', {
      type: AGUI.CUSTOM,
      name: AGUI_CUSTOM.APPROVAL_COUNTED,
      value: {
        interactionId: view.interactionId,
        approvals: { ...view.approvals },
      },
      runId: this.context.runId,
    });
  }

  /** Legacy's `calling_llm` snapshot on a call's first argument delta, once per message. */
  private announce(
    key: string,
    ts: string,
    out: ProjectedFrame[],
    delta: { fence: number; attempt: number } | null,
  ): void {
    if (this.announced.has(key)) return;
    this.announced.add(key);
    this.emit(
      out,
      delta === null ? 'j' : 'd',
      this.runState('calling_llm', ts),
      delta,
    );
  }

  private turnDone(turn: number): void {
    if (this.finishedTurns.has(turn)) return;
    this.finishedTurns.add(turn);
    this.toolTurns += 1;
  }

  private end(ts: string, seq: number, out: ProjectedFrame[]): void {
    if (this.terminalState !== null) return;
    // Legacy never starts a run its pre-run asked back, nor one whose model
    // could not start: no run frames at all.
    const asked = askedBeforeRunning(this.timeline.timeline);
    if (!asked && !failedBeforeStarting(this.timeline.timeline)) {
      this.start(ts, out);
    }
    if (failedModelCall(this.timeline.timeline)) {
      // The run ended on a model call that failed: legacy opens its message before the model fails it, then
      // retracts it (main #4442), whatever it had streamed.
      if (this.open !== null) {
        this.retract(out, this.open, null);
      } else {
        this.llmSnapshot(ts, out);
        const messageId = this.messageId(`${this.journalFence}.failed`, false);
        this.emit(out, 'j', {
          type: AGUI.TEXT_MESSAGE_START,
          messageId,
          role: 'assistant',
          runId: this.context.runId,
        });
        this.emit(out, 'j', this.textEnd(messageId));
        this.emitRetraction(out, messageId, null);
      }
    } else if (this.open !== null) {
      this.closeOpen(out, null);
    }
    // A question the run asked before any model call (legacy pre-run clarification).
    for (const question of this.timeline.timeline.questions) {
      if (this.timeline.call(question.callId) !== undefined) continue;
      this.emit(out, 'j', {
        type: AGUI.CUSTOM,
        name: AGUI_CUSTOM.ASK_USER_QUESTION,
        value: { status: question.status, toolData: question.toolData },
        runId: this.context.runId,
      });
    }
    this.terminalSeq = seq;
    const timeline = this.timeline.timeline;
    const completion = buildCompletion(timeline);
    if (completion === null) {
      const failure = runFailure(timeline);
      this.terminalState = {
        kind: 'error',
        code: failure.code,
        message: failure.message,
      };
      this.emit(out, 'j', this.runError(failure));
      return;
    }
    const answer = finalAnswer(timeline);
    const outcome = answer?.outcome ?? 'stopped';
    const status =
      outcome === 'stopped'
        ? 'cancelled'
        : outcome === 'error'
          ? 'failed'
          : 'completed';
    if (!asked) this.emit(out, 'j', this.runState(status, ts));
    const lastTurn = timeline.turns[timeline.turns.length - 1];
    const finalDelta = answerDelta({
      citations: answer?.citations ?? [],
      normalizedAnswer: answer?.answer ?? '',
      rawLength:
        outcome === 'stopped'
          ? 0
          : outcome === 'answered'
            ? codePointLength(lastTurn?.text ?? '')
            : codePointLength(answer?.answer ?? ''),
      ...(outcome === 'answered' &&
      answer?.confidence !== null &&
      answer?.confidence !== undefined
        ? { confidence: answer.confidence }
        : {}),
    });
    // Legacy's ask_user_question pause (main #3592) sends no answer delta: the card is the turn.
    const waitingInput =
      'status' in completion && completion.status === 'waiting_input';
    if (!waitingInput) {
      this.emit(out, 'j', {
        type: AGUI.STATE_DELTA,
        runId: this.context.runId,
        delta: finalDelta,
      });
    }
    this.emit(out, 'j', {
      type: AGUI.STATE_SNAPSHOT,
      runId: this.context.runId,
      snapshot: { final: true, ...completion },
    });
    this.terminalState = { kind: 'finished' };
  }

  /** Node's own terminal frame, after the transcript is readable. */
  finish(
    conversation: Record<string, unknown>,
    meta: Record<string, unknown>,
  ): ProjectedFrame[] {
    const out: ProjectedFrame[] = [];
    if (this.terminalState?.kind !== 'finished') return out;
    this.position = {
      ...this.position,
      seq: Math.max(this.position.seq, this.terminalSeq),
    };
    this.emit(out, 'j', {
      type: AGUI.RUN_FINISHED,
      result: { conversation, meta },
    });
    return out;
  }

  /** Node could not finish the run's stream (its conversation is gone). */
  fail(code: string, message: string): ProjectedFrame[] {
    const out: ProjectedFrame[] = [];
    this.terminalState = { kind: 'error', code, message };
    this.emit(out, 'j', this.runError({ code, message }));
    return out;
  }

  /**
   * The run's root `RUN_ERROR`. WP-100: a client reading the outcome frames also gets the typed outcome the saved
   * failure keeps (`failedOutcome`, the one reading of a failure), so the live chat and the reload word it alike.
   */
  private runError(failure: ReturnType<typeof runFailure>): AguiFrame {
    const { code, message, servers, stop, memoryLimitMb } = failure;
    return {
      type: AGUI.RUN_ERROR,
      message,
      code,
      ...(servers !== undefined ? { servers } : {}),
      // ADR-OPS-01 D5: the scope an operator stopped, which the web app words.
      ...(stop !== undefined ? { details: { ...stop } } : {}),
      // R31GVISO-FU-3: the memory limit an out-of-memory run met, which the web app names.
      ...(memoryLimitMb !== undefined ? { details: { memoryLimitMb } } : {}),
      ...(servesOutcomeFrames(this.context.frames)
        ? { runOutcome: failedOutcome(failure) }
        : {}),
    };
  }

  // ---- live deltas ----

  /**
   * The delta would open a message: whatever the worker journaled before the
   * model call (a consumed steering input) must be projected first.
   */
  startsMessage(delta: LiveDelta): boolean {
    if (delta.runId !== this.context.runId || this.terminalState !== null) {
      return false;
    }
    const key = `${delta.fence}.${delta.modelAttempt}`;
    return (
      !this.closed.has(key) && !this.lost.has(key) && this.open?.key !== key
    );
  }

  delta(delta: LiveDelta): ProjectedFrame[] {
    const out: ProjectedFrame[] = [];
    if (delta.runId !== this.context.runId || this.terminalState !== null)
      return out;
    if (delta.fence > this.journalFence) {
      this.held.push(delta);
      return out;
    }
    this.consume(delta, out);
    return out;
  }

  private consume(delta: LiveDelta, out: ProjectedFrame[]): void {
    if (delta.fence < this.journalFence) {
      this.observer.fenceDropped(delta.fence, this.journalFence);
      return;
    }
    const key = `${delta.fence}.${delta.modelAttempt}`;
    if (this.closed.has(key) || this.lost.has(key)) return;
    const identity = { fence: delta.fence, attempt: delta.modelAttempt };
    let open = this.open;
    // Frames ending the previous attempt and those opening this one are numbered in one run of `m`, so no
    // two share an id.
    let positioned = false;
    if (open !== null && open.key !== key) {
      if (
        delta.fence < open.fence ||
        (delta.fence === open.fence && delta.modelAttempt < open.attempt)
      ) {
        return;
      }
      // A later attempt on the same claim is a retry: the open call failed (the stream service projects a
      // call's journaled response before the next call's first delta). Its retraction is the end of its own
      // message, numbered after its last delta, so a resume from inside it re-consumes that message. A new
      // fence is a re-claim instead, whose journal restarts the message.
      if (delta.fence === open.fence) {
        this.retract(out, open, { fence: open.fence, attempt: open.attempt });
        this.position = { ...this.position, n: delta.n, m: 0 };
      } else {
        this.position = { ...this.position, n: delta.n, m: 0 };
        this.closeOpen(out, identity);
        positioned = true;
      }
      open = null;
    }
    if (open !== null && delta.n <= open.n) return;
    const expected = open === null ? 1 : open.n + 1;
    if (delta.n !== expected) {
      this.observer.deltaGap(
        delta.fence,
        delta.modelAttempt,
        expected,
        delta.n,
      );
      this.lost.add(key);
      if (open !== null) {
        this.position = { ...this.position, n: delta.n, m: 0 };
        this.closeOpen(out, identity);
      }
      return;
    }
    if (!positioned) this.position = { ...this.position, n: delta.n, m: 0 };
    if (open === null) {
      this.start(this.startedAt, out, identity);
      this.llmSnapshot(this.startedAt, out, identity);
      open = {
        key,
        fence: delta.fence,
        attempt: delta.modelAttempt,
        n: delta.n,
        messageId: this.messageId(key, false),
        shown: new ShownText(),
        reasoning: 'none',
        citations: [],
      };
      this.open = open;
      this.emitDelta(
        out,
        {
          type: AGUI.TEXT_MESSAGE_START,
          messageId: open.messageId,
          role: 'assistant',
          runId: this.context.runId,
        },
        delta.fence,
        delta.modelAttempt,
      );
    }
    open.n = delta.n;
    if (delta.kind === 'tool_call') {
      this.announce(key, this.startedAt, out, identity);
      return;
    }
    const text = typeof delta.data.text === 'string' ? delta.data.text : '';
    if (delta.kind === 'reasoning_delta') {
      // C4.10 `ui.reasoning` off: the run's reasoning never reaches the browser.
      if (this.timeline.timeline.display?.reasoning === false) return;
      this.reason(out, open, text, delta.fence, delta.modelAttempt);
      return;
    }
    if (open.reasoning === 'open') {
      this.closeReasoning(out, 'd', open, {
        fence: delta.fence,
        attempt: delta.modelAttempt,
      });
    }
    const shown = open.shown.push(text);
    if (shown.length > 0) {
      this.emitDelta(
        out,
        this.content(open.messageId, shown),
        delta.fence,
        delta.modelAttempt,
      );
    }
    this.liveAnswer(out, open, delta);
  }

  /**
   * The worker numbered the text so far (CITESTREAM): send it as legacy's
   * answer delta, so a citation is a link while the answer streams. The web
   * chat's only: a shared viewer never sees text that may turn out to be
   * narration (S-F17), and the Slack bot renders citations from the final
   * answer alone (`agui-stream.ts`).
   */
  private liveAnswer(
    out: ProjectedFrame[],
    open: OpenMessage,
    delta: LiveDelta,
  ): void {
    const { answer, citations } = delta.data;
    if (Array.isArray(citations)) open.citations = citations;
    // Without a source the numbering changes nothing a reader sees: no frame for it.
    if (typeof answer !== 'string' || open.citations.length === 0) return;
    if (this.context.audience !== 'owner') return;
    open.answer = {
      citations: open.citations,
      normalizedAnswer: streamingShownText(answer),
      rawLength: open.shown.shownLength,
    };
    this.emitDelta(
      out,
      {
        type: AGUI.STATE_DELTA,
        runId: this.context.runId,
        delta: answerDelta({ ...open.answer }),
      },
      delta.fence,
      delta.modelAttempt,
    );
  }

  private reason(
    out: ProjectedFrame[],
    open: OpenMessage,
    text: string,
    fence: number,
    attempt: number,
  ): void {
    if (text.length === 0) return;
    const reasoningId = this.reasoningId(open.key, false);
    if (open.reasoning !== 'open') {
      open.reasoning = 'open';
      this.emitDelta(
        out,
        { type: AGUI.REASONING_START, runId: this.context.runId },
        fence,
        attempt,
      );
      this.emitDelta(
        out,
        {
          type: AGUI.REASONING_MESSAGE_START,
          messageId: reasoningId,
          role: 'assistant',
          runId: this.context.runId,
        },
        fence,
        attempt,
      );
    }
    this.emitDelta(
      out,
      {
        type: AGUI.REASONING_MESSAGE_CONTENT,
        messageId: reasoningId,
        delta: text,
        runId: this.context.runId,
      },
      fence,
      attempt,
    );
  }

  private closeReasoning(
    out: ProjectedFrame[],
    kind: 'j' | 'd',
    open: OpenMessage,
    delta: { fence: number; attempt: number } | null,
  ): void {
    open.reasoning = 'closed';
    const reasoningId = this.reasoningId(open.key, false);
    this.emit(
      out,
      kind,
      {
        type: AGUI.REASONING_MESSAGE_END,
        messageId: reasoningId,
        runId: this.context.runId,
      },
      delta,
    );
    this.emit(
      out,
      kind,
      { type: AGUI.REASONING_END, runId: this.context.runId },
      delta,
    );
  }

  /** A whole reasoning message, for a response the client did not see stream. */
  private reasoningFrames(messageId: string, reasoning: string): AguiFrame[] {
    const runId = this.context.runId;
    return [
      { type: AGUI.REASONING_START, runId },
      {
        type: AGUI.REASONING_MESSAGE_START,
        messageId,
        role: 'assistant',
        runId,
      },
      {
        type: AGUI.REASONING_MESSAGE_CONTENT,
        messageId,
        delta: reasoning,
        runId,
      },
      { type: AGUI.REASONING_MESSAGE_END, messageId, runId },
      { type: AGUI.REASONING_END, runId },
    ];
  }

  /**
   * The client has message `fence.attempt` open but its deltas are gone (the
   * buffer expired, L-2, or the client fell behind, L-4): end it now; the
   * journal restarts it under a new id. `end` names the last frame the client
   * received, or says it already has this end (resuming from an `a:` id).
   */
  abandon(
    open: OpenAt,
    end: { sentAfter: string } | 'already_sent',
  ): ProjectedFrame[] {
    const key = `${open.fence}.${open.attempt}`;
    this.lost.add(key);
    if (this.open?.key === key) this.open = null;
    if (this.closed.has(key)) return [];
    this.replacing = {
      messageId: this.messageId(key, false),
      afterCursor: this.resumeAfter !== null,
      fence: open.fence,
      attempt: open.attempt,
    };
    if (end === 'already_sent') return [];
    const frame = this.forAudience(this.textEnd(this.messageId(key, false)));
    if (frame === null) return [];
    return [{ id: abandonFrameId(end.sentAfter), frame }];
  }

  /**
   * The open message's model call failed: end it and take back what it streamed, with the frames legacy sends
   * for a call that failed mid-stream (main #4442): `message_discarded` names it and a `/discarded` answer delta
   * puts the answer back to before the call. Nothing restarts it, so it is not replaced.
   */
  private retract(
    out: ProjectedFrame[],
    open: OpenMessage,
    delta: { fence: number; attempt: number } | null,
  ): void {
    if (open.reasoning === 'open')
      this.closeReasoning(out, delta === null ? 'j' : 'd', open, delta);
    this.open = null;
    this.emit(
      out,
      delta === null ? 'j' : 'd',
      this.textEnd(open.messageId),
      delta,
    );
    this.emitRetraction(out, open.messageId, delta);
    // Lost (in the ids) only once the whole retraction is out: a resume from inside it re-consumes the
    // message and sends the rest of the retraction.
    this.lost.add(open.key);
  }

  private emitRetraction(
    out: ProjectedFrame[],
    messageId: string,
    delta: { fence: number; attempt: number } | null,
  ): void {
    const kind = delta === null ? 'j' : 'd';
    this.emit(
      out,
      kind,
      {
        type: AGUI.CUSTOM,
        name: MESSAGE_DISCARDED,
        value: { messageId },
        runId: this.context.runId,
      },
      delta,
    );
    this.emit(
      out,
      kind,
      {
        type: AGUI.STATE_DELTA,
        runId: this.context.runId,
        delta: answerDelta({
          citations: [],
          normalizedAnswer: '',
          rawLength: 0,
          discarded: true,
        }),
      },
      delta,
    );
  }

  private closeOpen(
    out: ProjectedFrame[],
    delta: { fence: number; attempt: number } | null,
  ): void {
    const open = this.open;
    if (open === null) return;
    if (open.reasoning === 'open')
      this.closeReasoning(out, delta === null ? 'j' : 'd', open, delta);
    this.lost.add(open.key);
    this.open = null;
    this.replacing = {
      messageId: open.messageId,
      afterCursor: false,
      fence: open.fence,
      attempt: open.attempt,
    };
    this.emit(
      out,
      delta === null ? 'j' : 'd',
      this.textEnd(open.messageId),
      delta,
    );
  }

  /**
   * Applies an event that may add a notice and sends what it added. Node's notices with run.admitted are shown as
   * the run starts; one written mid-run is shown as it is journaled, once: the worker's (a refused code_task
   * hand-off, CTL-6), Node's beside child.spawned (SIGNGAP) and the ones Node derives (G-48). By identity: a later
   * time plan replaces the one before it in place (ADR-RUN-01 D4), and a repeated state notice is a new one (WP-89).
   */
  private noticed(event: JournalEvent, out: ProjectedFrame[]): void {
    const known = new Set(this.timeline.timeline.notices);
    this.timeline.apply(event);
    // Before the run started (a pre-run notice, QA23-F1) `start` shows it with Node's.
    if (!this.started) return;
    for (const notice of this.timeline.timeline.notices) {
      if (known.has(notice)) continue;
      this.emit(out, 'j', {
        type: AGUI.CUSTOM,
        name: NOTICE_FRAME,
        value: notice,
        runId: this.context.runId,
      });
    }
  }

  /** A child's progress nested under this run (P6.1); its own timeline has a route of its own (U3). */
  private child(event: JournalEvent, out: ProjectedFrame[]): void {
    const data = event.data ?? {};
    const childRunId = data['childRunId'];
    if (typeof childRunId !== 'string') return;
    const started = this.timeline.child(childRunId);
    let status = 'running';
    if (event.type === 'child.spawned') {
      this.currentChild = childRunId;
    } else {
      status = childStatus(data['outcome']);
      if (this.currentChild === childRunId) this.currentChild = null;
    }
    // A shared viewer's saved answer holds the child's `sub_agent` part (its job card), so its live stream names the
    // child too; `frameFor` keeps only the lifecycle frames, none of the child's steps (D14, CJFIX-B).
    const nested =
      this.context.audience === 'owner' || this.context.audience === 'shared';
    const step = `sub_agent:${started?.role ?? CHILD_ROLE}`;
    // The live row's model (MA1): the role's, else what this run is known to call by now.
    const model =
      started === undefined
        ? undefined
        : childModelLabel(this.timeline.timeline, started);
    if (nested && event.type === 'child.spawned') {
      // Legacy's sub-agent frames (`AGUIEventEmitter`): the web chat nests the child under them.
      const engine = this.timeline.codeTaskEngine;
      this.emit(out, 'j', {
        type: AGUI.STEP_STARTED,
        stepName: step,
        runId: this.context.runId,
        // A code task's child is the engine's run: the step names it ("Delegating to Codex"), not the role id.
        ...(started?.codeTaskGoal !== undefined && engine !== undefined
          ? { childEngine: engine }
          : {}),
      });
      this.emit(out, 'j', {
        type: AGUI.RUN_STARTED,
        threadId: this.context.conversationId,
        runId: childRunId,
        parentRunId: this.context.runId,
        ...(model !== undefined
          ? { model: { provider: model.provider, model: model.model } }
          : {}),
        ...(started?.codeTaskGoal !== undefined
          ? { codeTaskGoal: started.codeTaskGoal }
          : {}),
        ...(started?.goal !== undefined ? { goal: started.goal } : {}),
        ...(started?.codeTaskHandoff !== undefined
          ? { codeTaskHandoff: started.codeTaskHandoff }
          : {}),
      });
    }
    this.emit(out, 'j', {
      type: AGUI.STATE_SNAPSHOT,
      runId: childRunId,
      snapshot: {
        run_id: childRunId,
        agent_id: uuidFrom(`${childRunId}:agent`),
        trace_id: uuidFrom(`${this.context.runId}:trace`),
        parent_run_id: this.context.runId,
        role_name: CHILD_ROLE,
        status,
        current_turn: 0,
        current_tool: null,
        current_child_run_id: null,
        hil_request_id: null,
        goal_description: started?.taskId ?? null,
        started_at: started?.startedAt ?? event.ts,
        updated_at: event.ts,
      },
    });
    if (!nested || event.type !== 'child.completed') return;
    if (status === 'completed') {
      this.emit(out, 'j', {
        type: AGUI.RUN_FINISHED,
        runId: childRunId,
        parentRunId: this.context.runId,
      });
      this.emit(out, 'j', {
        type: AGUI.STEP_FINISHED,
        stepName: step,
        runId: this.context.runId,
      });
      return;
    }
    const errorType =
      typeof data['errorType'] === 'string' ? data['errorType'] : undefined;
    const limit = data['memoryLimitMb'];
    const memoryLimitMb =
      errorType === OUT_OF_MEMORY &&
      typeof limit === 'number' &&
      Number.isInteger(limit) &&
      limit > 0
        ? limit
        : undefined;
    // WP62-ERR: `message` is for people; the child's machine name rides in `errorType`, as the saved part keeps it.
    this.emit(out, 'j', {
      type: AGUI.RUN_ERROR,
      runId: childRunId,
      parentRunId: this.context.runId,
      message: childFailureMessage(status, errorType, memoryLimitMb),
      code: 'agent_error',
      ...(errorType !== undefined ? { errorType } : {}),
      // R32SECCOMP-FU-4: the live job card names the limit, as the saved part does.
      ...(memoryLimitMb !== undefined ? { details: { memoryLimitMb } } : {}),
    });
  }

  // ---- frame builders ----

  private messageId(key: string, restarted: boolean): string {
    return `msg_${hexId(`${this.context.runId}:${key}${restarted ? ':restart' : ''}`, 12)}`;
  }

  private reasoningId(key: string, restarted: boolean): string {
    return this.messageId(`${key}:reasoning`, restarted);
  }

  private content(messageId: string, delta: string): AguiFrame {
    return {
      type: AGUI.TEXT_MESSAGE_CONTENT,
      messageId,
      delta,
      runId: this.context.runId,
    };
  }

  private textEnd(messageId: string): AguiFrame {
    return {
      type: AGUI.TEXT_MESSAGE_END,
      messageId,
      runId: this.context.runId,
    };
  }

  private runState(
    status: string,
    ts: string,
    currentTool: string | null = null,
  ): AguiFrame {
    const runId = this.context.runId;
    return {
      type: AGUI.STATE_SNAPSHOT,
      runId,
      snapshot: {
        run_id: runId,
        agent_id: uuidFrom(`${runId}:agent`),
        trace_id: uuidFrom(`${runId}:trace`),
        parent_run_id: this.context.parentRunId ?? null,
        role_name:
          this.context.parentRunId !== undefined
            ? CHILD_ROLE
            : 'pipeshub-agent',
        status,
        current_turn: this.toolTurns,
        current_tool: currentTool,
        current_child_run_id: this.currentChild,
        hil_request_id: null,
        goal_description: this.timeline.timeline.goal ?? this.context.goal,
        started_at: this.startedAt || ts,
        updated_at: ts,
      },
    };
  }
}

/** What an approval card shows, from the journaled call (never model markup). */
export function approvalCard(view: ApprovalView): Record<string, unknown> {
  return {
    interactionId: view.interactionId,
    toolCallId: view.toolCallId,
    toolName: view.toolName,
    ...(view.displayName !== undefined
      ? { displayName: view.displayName }
      : {}),
    args: view.args,
    argsSummary: view.argsSummary ?? null,
    ...(view.reason !== undefined ? { reason: view.reason } : {}),
    // C3.71: the live card shows what the call would change and cost, as the saved part does.
    ...(view.effect !== undefined ? { effect: view.effect } : {}),
    // UXINT-1: journal order, the batch (model turns before it) and the plan step, as the saved part has them.
    seq: view.seq,
    afterTurns: view.afterTurns,
    // WP-113: asks open together share it; the chat groups only those.
    decidedBefore: view.decidedBefore,
    ...(view.stepId !== undefined ? { stepId: view.stepId } : {}),
    // WP-97: the chat says since when it waits.
    ...(view.requestedAt !== undefined
      ? { requestedAt: view.requestedAt }
      : {}),
  };
}

/** The turn calls a tool that is not the answer (legacy shows its arguments as a call). */
const callsTools = (turn: TurnView): boolean =>
  turn.calls.some((call) => call.streamsAnswer !== true);

/**
 * The turn's own text, the answer a call streamed after it, and whether a
 * call that is not the answer streams before that answer (call order).
 */
function splitAnswer(turn: TurnView): {
  head: string;
  answer: string;
  before: boolean;
} {
  const points = codePoints(turn.text);
  const from =
    turn.answerFrom === undefined
      ? points.length
      : Math.min(turn.answerFrom, points.length);
  const firstAnswer = turn.calls.findIndex((c) => c.streamsAnswer === true);
  const firstTool = turn.calls.findIndex((c) => c.streamsAnswer !== true);
  return {
    head: points.slice(0, from).join(''),
    answer: points.slice(from).join(''),
    before: firstTool >= 0 && (firstAnswer < 0 || firstTool < firstAnswer),
  };
}

/**
 * The closed message's text as the answer delta shows it: what the worker
 * numbered while it streamed (never taken back, so a shown link stays a
 * link), then the text shown after that numbering, as it streamed.
 */
const closedAnswer = (raw: string, shown: LiveAnswer | undefined): string => {
  const text = finalShownText(raw);
  return shown === undefined
    ? text
    : shown.normalizedAnswer + text.slice(shown.rawLength);
};

const answerDelta = (
  value: Record<string, unknown>,
): Array<{ op: 'replace'; path: string; value: unknown }> =>
  Object.entries(value).map(([key, entry]) => ({
    op: 'replace' as const,
    path: `/${key}`,
    value: entry,
  }));

/**
 * The owner's connect card (G-12): the item in words, where to connect it, and the instance its sign-in opens (the
 * card names it to the sign-in, never shows it); journal order, batch and step as an approval card has them (UXINT-1).
 */
export function connectCard(view: ConnectView): ConnectView {
  return { ...view };
}
