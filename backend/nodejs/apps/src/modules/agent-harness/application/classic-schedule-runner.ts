import type { AgentAccessPolicy } from '../ports/agent-access';
import { createHash } from 'node:crypto';
import type { RunInputSnapshot } from '../domain/execution';
import type {
  EndedScheduleRunStatus,
  ScheduleRunner,
  ScheduleRunRequest,
  ScheduleRunStart,
  ScheduleRunState,
} from '../ports/schedule-runner';
import {
  MAX_DIGEST_TOOLS,
  type DigestOutcome,
  type DigestTool,
  type RunDigest,
} from '../projection/run-digest';

/** What marks a classic turn as a schedule's firing (ADR-SCH-02 D3); the conversation records it as its origin. */
export interface ScheduledTurnMark {
  scheduleId: string;
  firingId: string;
  /** The conversation the turn opens, derived from the firing: a second start of it meets the one the first made. */
  conversationId: string;
  /** The conversation's title: the schedule's name. */
  title: string;
  /** An ask in the turn waits for the person until then (D4). */
  deadlineAt: number;
}

export interface ClassicTurnRequest {
  /** The actor's own credential, minted for this firing. */
  authorization: string;
  /** Absent: the assistant's chat. */
  agentKey?: string;
  /** The chat request the turn is, as the chat page would send it. */
  body: Record<string, unknown>;
  mark: ScheduledTurnMark;
  /** Told once when the turn's answer is saved or the turn fails. */
  ended?: () => void;
}

/** Starts a classic chat turn in this process, through the chat routes' own pipeline. */
export interface ClassicTurns {
  start(
    request: ClassicTurnRequest,
  ): Promise<{ conversationId: string } | { refused: string }>;
}

/** One part of a saved answer, as the runner reads it: what it called and how that went. */
export interface ClassicAnswerPart {
  type?: string;
  toolName?: string;
  displayName?: string;
  status?: string;
  approved?: boolean;
  approvalAnswer?: string;
  parts?: ClassicAnswerPart[];
}

export interface ClassicConversationState {
  status: 'in_progress' | 'complete' | 'failed' | 'stopped' | 'deleted';
  failReason?: string;
  /** The latest saved answer's parts and citations; null before the first answer. */
  answer: { parts: ClassicAnswerPart[]; citations: number } | null;
  startedAt: number | null;
  updatedAt: number | null;
}

/** The firing's conversation, read for the org it belongs to; null when there is none in that org. */
export interface ClassicConversations {
  read(
    orgId: string,
    conversationId: string,
  ): Promise<ClassicConversationState | null>;
  /** Records that the turn's ask expired unanswered, so the conversation says so. */
  expire(orgId: string, conversationId: string): Promise<void>;
}

export const ASK_EXPIRED = 'ask_expired';

/**
 * The conversation id of a firing's classic turn (ADR-SCH-02 D3): an ObjectId's 24 hex digits, as both document
 * stores take a caller's conversation id, derived from the org and the firing so every start of one firing names it.
 */
export const scheduledConversationId = (
  orgId: string,
  firingId: string,
): string =>
  createHash('sha256')
    .update(
      `schedule-firing-conversation\u0000${orgId}\u0000${firingId}`,
      'utf8',
    )
    .digest('hex')
    .slice(0, 24);
export const DEADLINE_PASSED = 'deadline_passed';

const assistantModes: ReadonlySet<string> = new Set([
  'agent',
  'internal_search',
  'web_search',
]);
const agentModes: ReadonlySet<string> = new Set([
  'quick',
  'auto',
  'react',
  'deep',
  'planExecute',
  'verification',
]);

/**
 * The classic chat request a schedule's input is. A mode the classic route does not take (a harness-only one) asks
 * for the route's own default with tools: the assistant's Agent mode, an agent's `auto`.
 */
export function classicTurnBody(
  input: RunInputSnapshot,
  agentKey: string | undefined,
): Record<string, unknown> {
  const mode = input.model.chatMode;
  const modes = agentKey === undefined ? assistantModes : agentModes;
  const chatMode = modes.has(mode)
    ? mode
    : agentKey === undefined
      ? 'agent'
      : 'auto';
  const model = input.model;
  // The record a connector event announced, named in the input only when its owner may read it (TR-03).
  const recordId = input.trigger?.event?.recordId;
  return {
    query: input.query,
    chatMode,
    recordIds: recordId !== undefined ? [recordId] : [],
    filters: {},
    ...(model.modelKey !== undefined ? { modelKey: model.modelKey } : {}),
    ...(model.modelName !== undefined ? { modelName: model.modelName } : {}),
    ...(model.modelFriendlyName !== undefined
      ? { modelFriendlyName: model.modelFriendlyName }
      : {}),
    ...(model.reasoningEffort !== undefined
      ? { reasoningEffort: model.reasoningEffort }
      : {}),
    ...(input.tools !== undefined ? { tools: [...input.tools] } : {}),
  };
}

const flatParts = (parts: readonly ClassicAnswerPart[]): ClassicAnswerPart[] =>
  parts.flatMap((part) => [part, ...flatParts(part.parts ?? [])]);

const isAsk = (part: ClassicAnswerPart): boolean =>
  (part.type === 'tool_call' && part.status === 'awaiting_approval') ||
  (part.type === 'approval' && part.status === 'pending');

/** Whether the saved answer ends on a call that waits for the person's approval. */
export const waitsForPerson = (parts: readonly ClassicAnswerPart[]): boolean =>
  flatParts(parts).some(isAsk);

/** The end-of-run summary of a classic turn, from its saved answer alone: names and counts, never content. */
export function classicDigest(
  state: ClassicConversationState,
  status: EndedScheduleRunStatus,
): RunDigest {
  const parts = flatParts(state.answer?.parts ?? []);
  const calls = parts.filter((p) => p.type === 'tool_call' && p.toolName);
  const tools = new Map<string, DigestTool>();
  for (const call of calls) {
    const name = String(call.toolName);
    const tool = tools.get(name) ?? {
      name,
      ...(call.displayName !== undefined
        ? { displayName: call.displayName }
        : {}),
      count: 0,
    };
    tool.count += 1;
    tools.set(name, tool);
  }
  const asked = parts.filter(
    (p) =>
      p.type === 'approval' ||
      p.approvalAnswer !== undefined ||
      p.status === 'awaiting_approval',
  );
  const outcome: DigestOutcome =
    status === 'completed'
      ? 'answered'
      : status === 'cancelled'
        ? 'cancelled'
        : state.failReason === ASK_EXPIRED
          ? 'needs_input'
          : 'failed';
  return {
    outcome,
    actions: {
      total: calls.length,
      failed: calls.filter((c) => c.status === 'failed').length,
      refused: calls.filter(
        (c) => c.status === 'blocked' || c.approvalAnswer === 'declined',
      ).length,
      tools: [...tools.values()]
        .sort((a, b) => b.count - a.count)
        .slice(0, MAX_DIGEST_TOOLS),
    },
    commands: { total: 0, failed: 0 },
    records: { read: state.answer?.citations ?? 0, changed: 0 },
    artifacts: [],
    files: { added: 0, modified: 0, deleted: 0, working: 0 },
    approvals: {
      asked: asked.length,
      approved: asked.filter(
        (p) =>
          p.approved === true ||
          p.approvalAnswer === 'allowed' ||
          p.status === 'approved',
      ).length,
      denied: asked.filter(
        (p) => p.approvalAnswer === 'declined' || p.status === 'denied',
      ).length,
      unanswered: asked.filter(
        (p) =>
          p.approvalAnswer === 'expired' ||
          p.status === 'awaiting_approval' ||
          p.status === 'pending' ||
          p.status === 'expired',
      ).length,
    },
    questions: { asked: 0, open: false },
    delegated: 0,
    cost: null,
    costEstimated: false,
    durationMs:
      state.startedAt !== null && state.updatedAt !== null
        ? Math.max(0, state.updatedAt - state.startedAt)
        : null,
  };
}

/**
 * A firing as the classic chat turn a person would start (ADR-SCH-02 D3): the assistant's or the agent's chat, as
 * the schedule's owner, in a new conversation named after the schedule. Its `runRef` is that conversation: approving
 * a held call there is the person's own next turn in it, and the run reads as ended once an answer no longer waits.
 */
/** How long past its deadline a scheduled turn's stop may take to land before the turn is taken as gone. */
export const STOP_GRACE_MS = 5 * 60 * 1000;

export class ClassicScheduleRunner implements ScheduleRunner {
  readonly kind = 'classic' as const;

  /**
   * `agents`: the agent authority, asked for the version a classic turn would run. Classic chat runs an agent's
   * latest published version only, so a schedule pinned to another (OWD-14) is refused rather than run on it.
   */
  constructor(
    private readonly turns: ClassicTurns,
    private readonly conversations: ClassicConversations,
    private readonly now: () => number = Date.now,
    private readonly agents?: Pick<AgentAccessPolicy, 'authorizeRun'>,
  ) {}

  /**
   * Starts the firing's turn at most once, however often the firer retries it (a failed settle, a crash before it,
   * two firers): the turn opens the conversation under an id derived from the firing, so the store's unique id is
   * what lets one start through. A start that finds that conversation, before or because its create was refused,
   * answers it, and the watch reads whether it is still running or ended.
   */
  /** Why a pinned agent version cannot run here; null when it can (unpinned, or the pin is the latest). */
  private async pinRefusal(
    request: ScheduleRunRequest,
  ): Promise<string | null> {
    if (
      request.agentKey === undefined ||
      request.agentVersion === undefined ||
      this.agents === undefined
    ) {
      return null;
    }
    const latest = await this.agents.authorizeRun({
      orgId: request.orgId,
      actorId: request.actorId,
      agentKey: request.agentKey,
      authorization: request.authorization,
    });
    if (latest === null) return 'agent_forbidden';
    return latest.definitionVersion === request.agentVersion
      ? null
      : 'agent_version_not_latest';
  }

  async start(request: ScheduleRunRequest): Promise<ScheduleRunStart> {
    const conversationId = scheduledConversationId(
      request.orgId,
      request.firingId,
    );
    const started = (): ScheduleRunStart => ({
      kind: 'started',
      runRef: conversationId,
      sessionId: conversationId,
      ...(request.trigger.maxCostUsd !== undefined
        ? { costCapIgnored: true }
        : {}),
    });
    if (
      (await this.conversations.read(request.orgId, conversationId)) !== null
    ) {
      return started();
    }
    const pin = await this.pinRefusal(request);
    if (pin !== null) return { kind: 'refused', reason: pin };
    const turn = await this.turns.start({
      authorization: request.authorization,
      ...(request.agentKey !== undefined ? { agentKey: request.agentKey } : {}),
      body: classicTurnBody(request.input, request.agentKey),
      mark: {
        scheduleId: request.scheduleId,
        firingId: request.firingId,
        conversationId,
        title: request.scheduleName,
        deadlineAt: request.deadlineAt,
      },
      ...(request.ended !== undefined ? { ended: request.ended } : {}),
    });
    if (!('refused' in turn)) return started();
    // Refused because another start of this firing made the conversation first: that start is the run.
    if (
      (await this.conversations.read(request.orgId, conversationId)) !== null
    ) {
      return started();
    }
    return { kind: 'refused', reason: turn.refused };
  }

  async status(
    orgId: string,
    runRef: string,
    deadlineAt: number | null,
  ): Promise<ScheduleRunState | null> {
    const state = await this.conversations.read(orgId, runRef);
    if (state === null) return null;
    const late = deadlineAt !== null && this.now() > deadlineAt;
    switch (state.status) {
      case 'deleted':
        return { status: 'cancelled' };
      case 'stopped':
        // Stopped at or after its deadline: by the deadline (`scheduledTurnDetachMs`). Stopped before it: by a
        // person, whenever the watch happens to read it. The stop time is when the conversation was last saved.
        return deadlineAt !== null &&
          state.updatedAt !== null &&
          state.updatedAt >= deadlineAt
          ? { status: 'failed', reason: DEADLINE_PASSED }
          : { status: 'cancelled' };
      case 'failed':
        return {
          status: 'failed',
          ...(state.failReason !== undefined
            ? { reason: state.failReason }
            : {}),
        };
      case 'in_progress':
        // The turn is stopped at its deadline (`scheduledTurnDetach`) and saves what it wrote; until that lands it
        // is still running, so the next firing waits. Past the grace only a process that died mid-turn leaves it so.
        return deadlineAt !== null && this.now() > deadlineAt + STOP_GRACE_MS
          ? { status: 'failed', reason: DEADLINE_PASSED }
          : { status: 'running' };
      case 'complete':
        break;
    }
    if (state.answer === null || !waitsForPerson(state.answer.parts)) {
      return { status: 'completed' };
    }
    if (!late) return { status: 'waiting_for_you' };
    await this.conversations.expire(orgId, runRef);
    return { status: 'failed', reason: ASK_EXPIRED };
  }

  async summary(
    orgId: string,
    runRef: string,
    status: EndedScheduleRunStatus,
  ): Promise<RunDigest | null> {
    const state = await this.conversations.read(orgId, runRef);
    return state === null ? null : classicDigest(state, status);
  }
}
