import { forEnumeratedOrg } from '../../../libs/context/orgContext';
import {
  HarnessMemoryRefusedError,
  HarnessNotFoundError,
} from '../domain/errors';
import { CHILD_MAX_DEPTH } from '../domain/child-runs';
import { childOf, HarnessRun } from '../domain/execution';
import {
  DECLASSIFIED_LABEL,
  isCurrent,
  isMemoryKind,
  isMemoryScope,
  MAX_MEMORY_LIST,
  MAX_PROPOSALS_PER_RUN,
  MEMORY_OFF,
  MemoryAdmission,
  MemoryEntry,
  MemoryKind,
  MemoryPin,
  MemoryPolicy,
  MemoryScope,
  memoryContent,
  memoryView,
  MemoryView,
  newestFirst,
  proposedMemoryId,
  recallableFor,
  recallCandidates,
  settlement,
  writeScopes,
} from '../domain/memory';
import { principalRefFor, PinnedRunSpec } from '../domain/run-spec';
import type { TurnLabel } from '../domain/turn-labels';
import { ExecutionStore } from '../ports/execution-store';
import {
  MemorySettings,
  MemoryStore,
  PersonalMemoryChoice,
} from '../ports/memory-store';
import {
  elapsedMs,
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';
import type { SharedCitationService } from './shared-citations';
import { performance } from 'perf_hooks';

/** Someone reading or changing memories with their own request. */
export interface MemoryViewer {
  orgId: string;
  userId: string;
  /** Their bearer credential: the label check is made with it. */
  authorization: string | undefined;
  requestId?: string;
}

/** Whom an admission recalls for. */
export interface MemoryRecallRequest {
  orgId: string;
  actorId: string;
  agentKey?: string;
  sessionId?: string;
  authorization?: string;
  /**
   * A run nobody is watching (a schedule's or a trigger's firing, P8a): it is
   * given its actor's memories, read with the actor's credential the firing
   * re-authorized, and proposes none (its input may be an outside payload,
   * and nobody is there to see what it kept).
   */
  unattended?: boolean;
}

/** A pinned memory as the worker gets it: content and the label its own-work label joins. */
export interface RecalledMemory {
  memoryId: string;
  scope: MemoryScope;
  kind: MemoryKind;
  content: string;
  label: TurnLabel;
}

export interface MemoryProposal {
  content: unknown;
  kind: unknown;
  scope: unknown;
  /** The id of the memory this one corrects (FIX-R2 C2); absent or null for a new fact. */
  replaces?: unknown;
}

export type ReviewDecision = 'accepted' | 'rejected';

/**
 * How old a pending proposal is before the sweeper settles it: well past a
 * run ending's own settlement, so the two rarely meet (meeting is harmless:
 * only pending proposals move).
 */
export const SETTLE_GRACE_MS = 60_000;
const OWN_SCOPES: readonly MemoryScope[] = ['user', 'session'];
const AGENT_SCOPES: readonly MemoryScope[] = ['agent'];

/** A memory id as a proposal may name it (`mem_` and 40 hex characters today). */
const MAX_MEMORY_ID_CHARS = 128;

/** Whether `entry` is the run's actor's own (user and session scope; session in this conversation) or its agent's. */
const ownedLike = (entry: MemoryEntry, run: HarnessRun): boolean => {
  switch (entry.scope) {
    case 'user':
      return entry.ownerId === run.actorId;
    case 'session':
      return entry.ownerId === run.actorId && entry.sessionId === run.sessionId;
    case 'agent':
      return run.agentKey !== undefined && entry.agentKey === run.agentKey;
  }
};

const memoryNotFound = (): HarnessMemoryRefusedError =>
  new HarnessMemoryRefusedError('MEMORY_NOT_FOUND', 'Memory not found');

/** The pin a claimed run carries (`x.pipeshub.memory`), or null. */
export function pinnedMemory(
  pinned: PinnedRunSpec | null | undefined,
): MemoryPin | null {
  const extension = pinned?.spec.extensions?.['x.pipeshub'] as
    { memory?: MemoryPin } | undefined;
  return extension?.memory ?? null;
}

export interface MemoryServiceOptions {
  store: MemoryStore;
  settings: MemorySettings;
  access: Pick<SharedCitationService, 'passingTurns'>;
  runs: Pick<ExecutionStore, 'readClaimSubject' | 'readEnding'>;
  /** Each person's own memory switch; absent: memory is on for everyone the policy grants it to. */
  choices?: PersonalMemoryChoice;
  logger?: HarnessLogger;
  now?: () => number;
}

/** Makes the memory service the harness binds; an edition binds its own at `AGENT_HARNESS_MEMORY_SERVICE_FACTORY`. */
export type MemoryServiceFactory = (
  options: MemoryServiceOptions,
) => MemoryService;
export const AGENT_HARNESS_MEMORY_SERVICE_FACTORY =
  'AgentHarnessMemoryServiceFactory';

/**
 * Governed memory (P7.3): the one place memories are recalled, proposed,
 * settled, reviewed, listed, edited and deleted. Every read that shows or
 * uses a memory's content passes its label with the reader's own credential
 * (`passingTurns`, P2.5a); the owner alone also sees their own memories of
 * unknown provenance, to review them. Logs carry ids and counts, never
 * content.
 */
export class MemoryService {
  protected readonly store: MemoryStore;
  private readonly settings: MemorySettings;
  private readonly access: Pick<SharedCitationService, 'passingTurns'>;
  private readonly runs: Pick<
    ExecutionStore,
    'readClaimSubject' | 'readEnding'
  >;
  private readonly choices: PersonalMemoryChoice | undefined;
  private readonly logger: HarnessLogger;
  protected readonly now: () => number;

  constructor(options: MemoryServiceOptions) {
    this.store = options.store;
    this.settings = options.settings;
    this.access = options.access;
    this.runs = options.runs;
    this.choices = options.choices;
    this.logger = options.logger ?? silentHarnessLogger;
    this.now = options.now ?? Date.now;
  }

  /** The org's policy; an unreadable one is off (fail closed). */
  async policy(orgId: string): Promise<MemoryPolicy> {
    try {
      return await this.settings.read(orgId);
    } catch (error) {
      this.logger.warn(
        'memory.policy_unreadable',
        harnessLogMeta('memory.policy_unreadable', {
          orgId,
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'off',
        }),
      );
      return MEMORY_OFF;
    }
  }

  /** Whether the person turned their own memory off; one that cannot be read is off, so nothing is kept unasked. */
  private async personOff(orgId: string, userId: string): Promise<boolean> {
    if (this.choices === undefined) return false;
    try {
      return await this.choices.memoryOff(orgId, userId);
    } catch (error) {
      this.logger.warn(
        'memory.choice_unreadable',
        harnessLogMeta('memory.choice_unreadable', {
          orgId,
          actorId: userId,
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'off',
        }),
      );
      return true;
    }
  }

  /** The viewer's own switch: whether their runs use and keep memories. */
  async ownChoice(viewer: MemoryViewer): Promise<{ remember: boolean }> {
    return { remember: !(await this.personOff(viewer.orgId, viewer.userId)) };
  }

  async setOwnChoice(
    viewer: MemoryViewer,
    remember: boolean,
  ): Promise<{ remember: boolean }> {
    if (this.choices === undefined) {
      throw new HarnessMemoryRefusedError(
        'MEMORY_WRITE_NOT_GRANTED',
        'Memory cannot be switched on this deployment',
      );
    }
    await this.choices.writeMemoryOff(viewer.orgId, viewer.userId, !remember);
    this.logger.info(
      'memory.choice_changed',
      harnessLogMeta('memory.choice_changed', {
        orgId: viewer.orgId,
        actorId: viewer.userId,
        requestId: viewer.requestId,
        remember,
      }),
    );
    return { remember };
  }

  /**
   * What a run admitted now may be given and may propose (decision 5),
   * checked with the sender's credential and never remembered. Undefined
   * when the policy grants nothing; any failure recalls nothing.
   */
  async admission(
    request: MemoryRecallRequest,
  ): Promise<MemoryAdmission | undefined> {
    const started = performance.now();
    const policy = await this.policy(request.orgId);
    if (policy.mode === 'off') return undefined;
    if (await this.personOff(request.orgId, request.actorId)) return undefined;
    const agentKey = request.agentKey ?? null;
    const subject = {
      actorId: request.actorId,
      agentKey,
      sessionId: request.sessionId ?? null,
    };
    let candidates: MemoryEntry[];
    try {
      const own = await this.store.list(request.orgId, {
        ownerId: request.actorId,
        scopes: OWN_SCOPES,
        statuses: ['accepted'],
        limit: MAX_MEMORY_LIST,
      });
      const agent =
        policy.mode === 'agent' && agentKey !== null
          ? await this.store.list(request.orgId, {
              agentKey,
              scopes: AGENT_SCOPES,
              statuses: ['accepted'],
              limit: MAX_MEMORY_LIST,
            })
          : [];
      candidates = recallCandidates([...own, ...agent], subject, policy);
    } catch (error) {
      this.logger.warn(
        'memory.recall_failed',
        harnessLogMeta('memory.recall_failed', {
          orgId: request.orgId,
          sessionId: request.sessionId,
          actorId: request.actorId,
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'recall_none',
        }),
      );
      candidates = [];
    }
    const passed = await this.passing(
      {
        orgId: request.orgId,
        userId: request.actorId,
        authorization: request.authorization,
      },
      candidates,
      request.sessionId,
    );
    const recalled = candidates
      .filter((entry) => passed.has(entry.memoryId))
      .map((entry) => ({ memoryId: entry.memoryId, scope: entry.scope }));
    const admission: MemoryAdmission = {
      recalled,
      writeScopes:
        request.unattended === true ? [] : writeScopes(policy, agentKey),
    };
    this.logger.info(
      'memory.recall_decided',
      harnessLogMeta('memory.recall_decided', {
        orgId: request.orgId,
        sessionId: request.sessionId,
        actorId: request.actorId,
        agentKey: request.agentKey,
        mode: policy.mode,
        candidates: candidates.length,
        recalled: recalled.length,
        writeScopes: admission.writeScopes,
        unattended: request.unattended === true,
        ms: elapsedMs(started),
      }),
    );
    return admission;
  }

  /**
   * The run's pinned memories as the worker renders them: those still
   * accepted and still the actor's (or the agent's), in pin order. A memory
   * deleted or narrowed since admission is not served; the policy turned off
   * since serves none.
   */
  async recallForRun(orgId: string, runId: string): Promise<RecalledMemory[]> {
    const subject = await this.runs.readClaimSubject(orgId, runId);
    if (subject === null) throw new HarnessNotFoundError();
    const pin = pinnedMemory(subject.pinned);
    if (pin === null || pin.recalled.length === 0) return [];
    const policy = await this.policy(orgId);
    if (policy.mode === 'off') return [];
    const run = subject.run;
    if (await this.personOff(orgId, run.actorId)) return [];
    const owner = {
      actorId: run.actorId,
      agentKey: run.agentKey ?? null,
      sessionId: run.sessionId,
    };
    const byId = new Map(
      (await this.store.byIds(orgId, pin.recalled)).map((entry) => [
        entry.memoryId,
        entry,
      ]),
    );
    const served: RecalledMemory[] = [];
    for (const memoryId of pin.recalled) {
      const entry = byId.get(memoryId);
      if (
        entry?.status !== 'accepted' ||
        entry.label?.complete !== true ||
        // Replaced by a correction accepted since this run was admitted (FIX-R2 C2).
        !isCurrent(entry) ||
        // A run in a shared context (SS2) is never served personal memory, whatever its pin names.
        (entry.scope === 'user' && run.sessionRole !== undefined) ||
        !recallableFor(entry, owner, policy)
      ) {
        continue;
      }
      served.push({
        memoryId: entry.memoryId,
        scope: entry.scope,
        kind: entry.kind,
        content: entry.content,
        label: entry.label,
      });
    }
    this.logger.info(
      'memory.recall_served',
      harnessLogMeta('memory.recall_served', {
        orgId,
        sessionId: run.sessionId,
        runId,
        pinned: pin.recalled.length,
        served: served.length,
      }),
    );
    return served;
  }

  /** A run's model proposes a memory (decision 7): stored pending until the run ends. */
  async propose(
    orgId: string,
    runId: string,
    proposal: MemoryProposal,
  ): Promise<{
    memoryId: string;
    status: MemoryEntry['status'];
    created: boolean;
  }> {
    const subject = await this.runs.readClaimSubject(orgId, runId);
    if (subject === null) throw new HarnessNotFoundError();
    const run = subject.run;
    const pin = pinnedMemory(subject.pinned);
    const content = memoryContent(proposal.content);
    if (
      content === null ||
      !isMemoryKind(proposal.kind) ||
      !isMemoryScope(proposal.scope)
    ) {
      throw this.refused(
        run,
        'MEMORY_INVALID',
        'A memory is 1 to 500 characters of text, a kind and a scope',
      );
    }
    const scope = proposal.scope;
    if (
      !pin?.writeScopes.includes(scope) ||
      (await this.personOff(orgId, run.actorId))
    ) {
      throw this.refused(
        run,
        'MEMORY_WRITE_NOT_GRANTED',
        'This run may not keep memories in that scope',
      );
    }
    if (scope === 'agent' && run.agentKey === undefined) {
      throw this.refused(
        run,
        'MEMORY_WRITE_NOT_GRANTED',
        'Only an agent run keeps agent memories',
      );
    }
    // A child's proposals are its turn's (DEEPROUTE2): they settle with the root run's label, which joins the
    // child's own. Settled at the child's end, which has no transcript and so no label, they were held for review
    // and never recalled (an engine asked to remember during a hand-off).
    const turnRunId = await this.turnRunId(run);
    const replaces = await this.replaced(
      run,
      turnRunId,
      pin,
      scope,
      proposal.replaces,
    );
    const memoryId = proposedMemoryId({
      orgId,
      runId: turnRunId,
      scope,
      kind: proposal.kind,
      content,
    });
    const existing = await this.store.get(orgId, memoryId);
    if (existing !== null) {
      return { memoryId, status: existing.status, created: false };
    }
    const made = await this.store.list(orgId, {
      runId: turnRunId,
      limit: MAX_PROPOSALS_PER_RUN,
    });
    if (made.length >= MAX_PROPOSALS_PER_RUN) {
      throw this.refused(
        run,
        'MEMORY_LIMIT',
        `A run keeps at most ${String(MAX_PROPOSALS_PER_RUN)} memories`,
      );
    }
    const now = this.now();
    const { memory, created } = await this.store.propose({
      memoryId,
      orgId,
      scope,
      ownerId: run.actorId,
      agentKey: run.agentKey ?? null,
      sessionId: run.sessionId,
      runId: turnRunId,
      origin: 'model',
      kind: proposal.kind,
      content,
      status: 'pending',
      label: null,
      createdAt: now,
      updatedAt: now,
      reviewedBy: null,
      reviewedAt: null,
      replaces,
      supersededBy: null,
    });
    this.logger.info(
      'memory.proposed',
      harnessLogMeta('memory.proposed', {
        orgId,
        sessionId: run.sessionId,
        runId,
        ...(turnRunId !== runId ? { turnRunId } : {}),
        memoryId,
        scope,
        kind: memory.kind,
        created,
        replaces,
      }),
    );
    return { memoryId, status: memory.status, created };
  }

  /**
   * What a correction replaces (FIX-R2 C2): a memory the run was given (its
   * pin names it, and it is still the actor's or the agent's), in the same
   * scope. A run correcting its own proposal, not yet settled, withdraws that
   * proposal instead: it never became a memory, so there is nothing to keep.
   */
  /** The root run of `run`'s turn: itself, or the run its chain of parents starts from. */
  private async turnRunId(run: HarnessRun): Promise<string> {
    let current = run;
    for (let depth = 0; depth < CHILD_MAX_DEPTH; depth += 1) {
      const parent = childOf(current);
      if (parent === null) return current.runId;
      const subject = await this.runs.readClaimSubject(
        current.orgId,
        parent.runId,
      );
      if (subject === null) return parent.runId;
      current = subject.run;
    }
    return current.runId;
  }

  private async replaced(
    run: HarnessRun,
    turnRunId: string,
    pin: MemoryPin | null,
    scope: MemoryScope,
    replaces: unknown,
  ): Promise<string | null> {
    if (replaces === undefined || replaces === null) return null;
    if (
      typeof replaces !== 'string' ||
      replaces.length === 0 ||
      replaces.length > MAX_MEMORY_ID_CHARS
    ) {
      throw this.refused(
        run,
        'MEMORY_INVALID',
        '`replaces` is the id of a memory',
      );
    }
    const entry = await this.store.get(run.orgId, replaces);
    const unknown = (): HarnessMemoryRefusedError =>
      this.refused(
        run,
        'MEMORY_REPLACE_UNKNOWN',
        'A correction replaces a memory this run was given, in the same scope',
      );
    if (entry === null || entry.scope !== scope || !ownedLike(entry, run)) {
      throw unknown();
    }
    if (entry.runId === turnRunId && entry.status === 'pending') {
      await this.store.delete(run.orgId, entry.memoryId);
      return null;
    }
    if (
      entry.status !== 'accepted' ||
      !(pin?.recalled.includes(replaces) ?? false)
    ) {
      throw unknown();
    }
    return replaces;
  }

  /** An accepted correction retires the memory it replaces; that one stays for audit and erasure only. */
  private async supersede(correction: MemoryEntry, now: number): Promise<void> {
    const replaces = correction.replaces ?? null;
    if (replaces === null) return;
    const old = await this.store.update(
      correction.orgId,
      replaces,
      { supersededBy: correction.memoryId, updatedAt: now },
      'accepted',
    );
    this.logger.info(
      'memory.superseded',
      harnessLogMeta('memory.superseded', {
        orgId: correction.orgId,
        sessionId: correction.sessionId ?? undefined,
        runId: correction.runId ?? undefined,
        memoryId: replaces,
        supersededBy: correction.memoryId,
        found: old !== null,
      }),
    );
  }

  /**
   * The run ended with its turn label (decision 2): its pending memories
   * settle by the policy. A policy that cannot be read sends them to review.
   * Settling twice changes nothing (only pending ones move). `null`: the run
   * ended with no label (H2), which decision 3 treats as incomplete. A
   * person who turned their memory off while the run went on keeps none.
   */
  async settleRun(run: HarnessRun, label: TurnLabel | null): Promise<void> {
    const pending = await this.store.list(run.orgId, {
      runId: run.runId,
      statuses: ['pending'],
      limit: MAX_PROPOSALS_PER_RUN,
    });
    if (pending.length === 0) return;
    let policy: MemoryPolicy;
    try {
      policy = await this.settings.read(run.orgId);
    } catch {
      policy = { mode: 'user', review: 'always' };
    }
    const off = await this.personOff(run.orgId, run.actorId);
    const counts = { accepted: 0, proposed: 0, discarded: 0 };
    const now = this.now();
    const accepted: MemoryEntry[] = [];
    for (const entry of pending) {
      const decided = off
        ? 'discarded'
        : settlement(entry.scope, label, policy);
      if (decided === 'discarded') {
        if (await this.store.delete(run.orgId, entry.memoryId)) {
          counts.discarded += 1;
        }
        continue;
      }
      const updated = await this.store.update(
        run.orgId,
        entry.memoryId,
        { status: decided, label, updatedAt: now },
        'pending',
      );
      if (updated !== null) counts[decided] += 1;
      if (updated?.status === 'accepted') accepted.push(updated);
    }
    for (const correction of accepted) await this.supersede(correction, now);
    const event =
      label === null ? 'memory.settled_unlabelled' : 'memory.settled';
    this.logger.info(
      event,
      harnessLogMeta(event, {
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        labelComplete: label?.complete ?? false,
        labelIntegrity: label?.integrity ?? 'unknown',
        review: policy.review,
        ...counts,
      }),
    );
  }

  /**
   * The sweeper's backstop for settlements a run's ending missed (the
   * process died between the commit and its listener): runs that ended with
   * proposals still pending settle with their stored label. Proposals of a
   * running run wait for its end. Answers how many runs settled.
   */
  async settleDue(limit: number): Promise<number> {
    let settled = 0;
    for (const pending of await this.store.pendingRuns(
      this.now() - SETTLE_GRACE_MS,
      limit,
    )) {
      try {
        const done = await forEnumeratedOrg(
          pending.orgId,
          'agent-harness.memory-settle',
          async () => {
            const ending = await this.runs.readEnding(
              pending.orgId,
              pending.runId,
            );
            if (ending === null) return false;
            await this.settleRun(ending.run, ending.label);
            return true;
          },
        );
        if (done) settled += 1;
      } catch (error) {
        this.logger.warn(
          'memory.settle_failed',
          harnessLogMeta('memory.settle_failed', {
            orgId: pending.orgId,
            runId: pending.runId,
            errorType: error instanceof Error ? error.name : typeof error,
            choice: 'left_pending',
          }),
        );
      }
    }
    return settled;
  }

  /** A run-ending listener: a failure is logged and never reaches the run's ending. */
  settleQuietly(run: HarnessRun, label: TurnLabel | null): void {
    this.settleRun(run, label).catch((error: unknown) => {
      this.logger.warn(
        'memory.settle_failed',
        harnessLogMeta('memory.settle_failed', {
          orgId: run.orgId,
          sessionId: run.sessionId,
          runId: run.runId,
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'left_pending',
        }),
      );
    });
  }

  /**
   * The viewer's own memories (user and session scope) they may see: those
   * whose label passes with their credential, and their own pending or
   * proposed ones of unknown provenance, which only they can review.
   */
  async listOwn(
    viewer: MemoryViewer,
    statuses?: readonly MemoryEntry['status'][],
  ): Promise<MemoryView[]> {
    const entries = await this.store.list(viewer.orgId, {
      ownerId: viewer.userId,
      scopes: OWN_SCOPES,
      ...(statuses !== undefined ? { statuses } : {}),
      limit: MAX_MEMORY_LIST,
    });
    const visible = await this.visibleOwn(viewer, entries);
    return entries
      .filter((entry) => isCurrent(entry) && visible.has(entry.memoryId))
      .map(memoryView);
  }

  async editOwn(
    viewer: MemoryViewer,
    memoryId: string,
    content: unknown,
  ): Promise<MemoryView> {
    const entry = await this.ownVisible(viewer, memoryId);
    const text = memoryContent(content);
    if (text === null) {
      throw new HarnessMemoryRefusedError(
        'MEMORY_INVALID',
        'A memory is 1 to 500 characters of text',
      );
    }
    const updated = await this.store.update(viewer.orgId, memoryId, {
      content: text,
      origin: 'user',
      updatedAt: this.now(),
    });
    if (updated === null) throw memoryNotFound();
    this.logGovernance('memory.edited', viewer, entry);
    return memoryView(updated);
  }

  /** The owner may always delete their own memory, whether or not they may still read it. */
  async deleteOwn(viewer: MemoryViewer, memoryId: string): Promise<void> {
    const entry = await this.store.get(viewer.orgId, memoryId);
    if (entry?.ownerId !== viewer.userId || !OWN_SCOPES.includes(entry.scope)) {
      throw memoryNotFound();
    }
    await this.store.delete(viewer.orgId, memoryId);
    this.logGovernance('memory.deleted', viewer, entry);
  }

  async reviewOwn(
    viewer: MemoryViewer,
    memoryId: string,
    decision: ReviewDecision,
  ): Promise<MemoryView | null> {
    const entry = await this.ownVisible(viewer, memoryId);
    return this.review(viewer, entry, decision);
  }

  protected async review(
    viewer: MemoryViewer,
    entry: MemoryEntry,
    decision: ReviewDecision,
  ): Promise<MemoryView | null> {
    if (entry.status === 'accepted') {
      throw new HarnessMemoryRefusedError(
        'MEMORY_NOT_REVIEWABLE',
        'This memory was already accepted',
      );
    }
    if (decision === 'rejected') {
      await this.store.delete(viewer.orgId, entry.memoryId);
      this.logGovernance('memory.reviewed', viewer, entry, { decision });
      return null;
    }
    const now = this.now();
    const updated = await this.store.update(
      viewer.orgId,
      entry.memoryId,
      {
        status: 'accepted',
        label:
          entry.label?.complete === true ? entry.label : DECLASSIFIED_LABEL,
        reviewedBy: principalRefFor(viewer.userId),
        reviewedAt: now,
        updatedAt: now,
      },
      entry.status,
    );
    if (updated === null) {
      throw new HarnessMemoryRefusedError(
        'MEMORY_NOT_REVIEWABLE',
        'This memory changed meanwhile; reload it',
      );
    }
    await this.supersede(updated, now);
    this.logGovernance('memory.reviewed', viewer, entry, {
      decision,
      declassified: entry.label?.complete !== true,
    });
    return memoryView(updated);
  }

  /** The owner's memory, if they may see it now (decision 4); not found otherwise. */
  private async ownVisible(
    viewer: MemoryViewer,
    memoryId: string,
  ): Promise<MemoryEntry> {
    const entry = await this.store.get(viewer.orgId, memoryId);
    if (
      entry?.ownerId !== viewer.userId ||
      !OWN_SCOPES.includes(entry.scope) ||
      !(await this.visibleOwn(viewer, [entry])).has(memoryId)
    ) {
      throw memoryNotFound();
    }
    return entry;
  }

  private async visibleOwn(
    viewer: MemoryViewer,
    entries: readonly MemoryEntry[],
  ): Promise<Set<string>> {
    const visible = await this.passing(viewer, entries);
    for (const entry of entries) {
      if (entry.label?.complete !== true && entry.status !== 'accepted') {
        visible.add(entry.memoryId);
      }
    }
    return visible;
  }

  /** The memories whose label passes for the viewer, one batch, nothing cached. */
  protected async passing(
    viewer: MemoryViewer,
    entries: readonly MemoryEntry[],
    sessionId?: string,
  ): Promise<Set<string>> {
    const labelled = entries.filter((entry) => entry.label?.complete === true);
    if (labelled.length === 0) return new Set();
    return this.access.passingTurns(
      {
        authorization: viewer.authorization,
        orgId: viewer.orgId,
        ...(sessionId !== undefined ? { sessionId } : {}),
        ...(viewer.requestId !== undefined
          ? { requestId: viewer.requestId }
          : {}),
        surface: 'memory',
      },
      labelled
        .sort(newestFirst)
        .map((entry) => ({ id: entry.memoryId, label: entry.label })),
    );
  }

  private refused(
    run: HarnessRun,
    code:
      | 'MEMORY_INVALID'
      | 'MEMORY_WRITE_NOT_GRANTED'
      | 'MEMORY_LIMIT'
      | 'MEMORY_REPLACE_UNKNOWN',
    message: string,
  ): HarnessMemoryRefusedError {
    this.logger.warn(
      'memory.refused',
      harnessLogMeta('memory.refused', {
        orgId: run.orgId,
        sessionId: run.sessionId,
        runId: run.runId,
        reason: code,
      }),
    );
    return new HarnessMemoryRefusedError(code, message);
  }

  protected logGovernance(
    event: string,
    viewer: MemoryViewer,
    entry: MemoryEntry,
    extra: Record<string, unknown> = {},
  ): void {
    this.logger.info(
      event,
      harnessLogMeta(event, {
        orgId: viewer.orgId,
        actorId: viewer.userId,
        requestId: viewer.requestId,
        memoryId: entry.memoryId,
        scope: entry.scope,
        status: entry.status,
        ...extra,
      }),
    );
  }
}
