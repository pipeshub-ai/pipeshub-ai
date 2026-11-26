/**
 * QA13-6/7/9 (JRN-23, WRK-31): the run, its attempts, leases and
 * interactions as one state machine. `RunWorld` applies an operation to a
 * real `ExecutionService` over a contract backend (in-memory or Mongo);
 * `RunModel` is the pure specification: what each operation must answer
 * and what the store holds after it. Both see harness v2 agent runs in a
 * few sessions of one tenant, on the store's clock.
 */
import { expect } from 'chai';
import fc from 'fast-check';
import { InMemoryRunWakeup } from '../../../../src/modules/agent-harness/adapters/in-memory-interactions';
import { InteractionService } from '../../../../src/modules/agent-harness/application/interaction-service';
import { HarnessConflictError } from '../../../../src/modules/agent-harness/domain/errors';
import {
  canWorkerTransition,
  CommitBatch,
  HarnessRun,
  RunStatus,
  RunTransition,
  TERMINAL_RUN_STATUSES,
  WORKER_OWNED_RUN_STATUSES,
} from '../../../../src/modules/agent-harness/domain/execution';
import { RESUMABLE_RUN_STATUSES } from '../../../../src/modules/agent-harness/domain/interactions';
import { STEERABLE_RUN_STATUSES } from '../../../../src/modules/agent-harness/domain/steering';
import { describeViolations, journalHistoryViolations } from '../../../support/harness/journal-history';
import { ContractHarness } from './execution-store-contract';
import { HARNESS_ENABLED } from './rollout';
import { workerEvent } from './journal-fixtures';

export const START = 1_000;
export const DEADLINE_MS = 9_000;
export const LEASE_MS = 1_000;
const AGENT_RUN = { agentKey: 'agent-1', authorization: 'Bearer user' };
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const IDLE: ReadonlySet<RunStatus> = new Set([
  'queued',
  'waiting_approval',
  'paused',
  'recovering',
  'needs_reconciliation',
]);

export type CommitStep =
  | 'progress'
  | 'ask'
  | 'suspend_approval'
  | 'suspend_input'
  | 'yield'
  | 'complete'
  | 'fail'
  | 'cancelled'
  | 'recovering'
  | 'needs_reconciliation';

export type Op =
  | { kind: 'admit'; session: number }
  | { kind: 'claim'; run: number; second: boolean }
  | { kind: 'renew'; run: number; stale: boolean }
  | { kind: 'commit'; run: number; step: CommitStep; stale: boolean }
  | { kind: 'duplicate'; run: number; changed: boolean }
  | { kind: 'release'; run: number }
  | { kind: 'cancel'; run: number }
  | { kind: 'resolve'; run: number; open: boolean; approve: boolean; retry: boolean }
  | { kind: 'advance'; ms: number }
  | { kind: 'sweep' }
  | { kind: 'relay' };

/** Steps a worker commits, the ones that ask a person more often (they open the interaction rules). */
const STEP: fc.Arbitrary<CommitStep> = fc.oneof(
  { weight: 3, arbitrary: fc.constant<CommitStep>('suspend_approval') },
  { weight: 2, arbitrary: fc.constantFrom<CommitStep>('ask', 'yield') },
  {
    weight: 1,
    arbitrary: fc.constantFrom<CommitStep>(
      'progress',
      'suspend_input',
      'complete',
      'fail',
      'cancelled',
      'recovering',
      'needs_reconciliation',
    ),
  },
);

const runIndex = fc.nat(1);
export const opArbitrary: fc.Arbitrary<Op> = fc.oneof(
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('admit' as const), session: fc.nat(2) }) },
  { weight: 3, arbitrary: fc.record({ kind: fc.constant('claim' as const), run: runIndex, second: fc.boolean() }) },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('renew' as const), run: runIndex, stale: fc.boolean() }) },
  {
    weight: 6,
    arbitrary: fc.record({
      kind: fc.constant('commit' as const),
      run: runIndex,
      step: STEP,
      stale: fc.boolean(),
    }),
  },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('duplicate' as const), run: runIndex, changed: fc.boolean() }) },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('release' as const), run: runIndex }) },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('cancel' as const), run: runIndex }) },
  {
    weight: 3,
    arbitrary: fc.record({
      kind: fc.constant('resolve' as const),
      run: runIndex,
      open: fc.boolean(),
      approve: fc.boolean(),
      retry: fc.boolean(),
    }),
  },
  {
    weight: 2,
    arbitrary: fc.record({
      kind: fc.constant('advance' as const),
      ms: fc.oneof({ weight: 4, arbitrary: fc.constantFrom(100, 600, 1_500) }, { weight: 1, arbitrary: fc.constant(5_000) }),
    }),
  },
  { weight: 2, arbitrary: fc.record({ kind: fc.constant('sweep' as const) }) },
  { weight: 1, arbitrary: fc.record({ kind: fc.constant('relay' as const) }) },
);

const TRANSITION_TO: Record<CommitStep, RunStatus | null> = {
  progress: null,
  ask: null,
  suspend_approval: 'waiting_approval',
  suspend_input: 'paused',
  yield: 'queued',
  complete: 'completed',
  fail: 'failed',
  cancelled: 'cancelled',
  recovering: 'recovering',
  needs_reconciliation: 'needs_reconciliation',
};
/** A harness v2 transition is one the journal records (a run.* event, QA13-F2). */
const V2_STATUSES: ReadonlySet<RunStatus> = new Set([
  'queued',
  'waiting_approval',
  'paused',
  'completed',
  'failed',
  'cancelled',
]);

/** Every sequence starts with one run admitted, so the first commands have a run to act on. */
export const FIRST_RUN: Op = { kind: 'admit', session: 0 };

/** What an operation answered: done, refused with a code, or not applicable to this state (no call made). */
export type Outcome = { ok: true } | { ok: false; code: string } | { skipped: true };

interface Token {
  workerId: string;
  attemptId: string;
  fence: number;
}

interface WorldRun {
  orgId: string;
  runId: string;
  sessionId: string;
  tokens: Token[];
  lastCommit: CommitBatch | null;
  interactions: number;
  resolved: Array<{ interactionId: string; commandId: string }>;
}

const codeOf = (error: unknown): string => {
  if (error instanceof HarnessConflictError) return error.code;
  if (error instanceof Error) return error.name;
  return String(error);
};

async function outcome(work: () => Promise<unknown>): Promise<Outcome> {
  try {
    await work();
    return { ok: true };
  } catch (error) {
    return { ok: false, code: codeOf(error) };
  }
}

export interface RunView {
  runId: string;
  status: RunStatus;
  fence: number;
  attempts: number;
  open: string[];
  deadline: number;
}

export interface WorldSnapshot {
  runs: RunView[];
  active: Array<string | null>;
  wakes: number[];
  journal: string[][];
}

/** The real side: one tenant's runs on `h`, and what its workers hold. */
export class RunWorld {
  readonly runs: WorldRun[] = [];
  readonly sessions: string[] = [];
  private commands = 0;

  constructor(readonly h: ContractHarness) {
    h.setRollout(V2);
  }

  private next(prefix: string): string {
    this.commands += 1;
    return `${prefix}-${String(this.commands)}`;
  }

  run(index: number): WorldRun | undefined {
    return this.runs.length === 0 ? undefined : this.runs[index % this.runs.length];
  }

  async apply(op: Op): Promise<Outcome> {
    switch (op.kind) {
      case 'admit':
        return this.admit(op.session);
      case 'advance':
        this.h.setNow(this.h.clock.now() + op.ms);
        return { ok: true };
      case 'sweep':
        return outcome(() => this.h.service.sweep(100));
      case 'relay':
        return { ok: true };
      default:
        break;
    }
    const run = this.run(op.run);
    if (run === undefined) return { skipped: true };
    switch (op.kind) {
      case 'claim':
        return this.claim(run, op.second);
      case 'renew':
        return this.withToken(run, op.stale, (token) =>
          outcome(() =>
            this.h.service.renew({ orgId: run.orgId, runId: run.runId, ...token, leaseDurationMs: LEASE_MS }),
          ),
        );
      case 'commit':
        return this.withToken(run, op.stale, (token) => this.commit(run, token, op.step));
      case 'duplicate':
        return this.duplicate(run, op.changed);
      case 'release':
        return this.withToken(run, false, (token) =>
          outcome(() =>
            this.h.service.release({ orgId: run.orgId, runId: run.runId, ...token, reason: 'drain' }),
          ),
        );
      case 'cancel':
        return this.cancel(run);
      case 'resolve':
        return this.resolve(run, op);
      default:
        return { skipped: true };
    }
  }

  private async admit(session: number): Promise<Outcome> {
    const sessionId = this.sessions[session];
    let admitted: Awaited<ReturnType<ContractHarness['admit']>>;
    try {
      admitted = await this.h.admit(sessionId === undefined ? AGENT_RUN : { ...AGENT_RUN, sessionId });
    } catch (error) {
      return { ok: false, code: codeOf(error) };
    }
    if (sessionId === undefined) this.sessions.push(admitted.sessionId);
    if (admitted.queued === undefined) {
      this.runs.push({
        orgId: admitted.run.orgId,
        runId: admitted.run.runId,
        sessionId: admitted.sessionId,
        tokens: [],
        lastCommit: null,
        interactions: 0,
        resolved: [],
      });
    }
    return { ok: true };
  }

  /** Either of two workers: the same one claiming again after its lease lapsed holds a stale token of its own. */
  private async claim(run: WorldRun, second: boolean): Promise<Outcome> {
    const token = { workerId: second ? 'worker-b' : 'worker-a', attemptId: this.next('attempt'), fence: 0 };
    try {
      const claimed = await this.h.service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: token.workerId,
        attemptId: token.attemptId,
        leaseDurationMs: LEASE_MS,
      });
      run.tokens.push({ ...token, fence: claimed.run.fence });
      return { ok: true };
    } catch (error) {
      return { ok: false, code: codeOf(error) };
    }
  }

  /** The current token, or (stale) the one before it; skipped when the run has no such token. */
  private async withToken(
    run: WorldRun,
    stale: boolean,
    work: (token: Token) => Promise<Outcome>,
  ): Promise<Outcome> {
    const token = run.tokens.at(stale ? -2 : -1);
    return token === undefined ? { skipped: true } : work(token);
  }

  private async commit(run: WorldRun, token: Token, step: CommitStep): Promise<Outcome> {
    const stored = await this.h.service.get(run.orgId, run.runId);
    const ix = `ix-${String(run.interactions + 1)}`;
    const requested = workerEvent('interaction.requested', {
      interactionId: ix,
      kind: 'approval',
      bindingDigest: `sha256:${'1'.repeat(64)}`,
    });
    const consumed = (stored?.pendingInputs ?? []).map((messageSeq) =>
      workerEvent('input.consumed', { messageSeq }),
    );
    const transitions: Record<CommitStep, [CommitBatch['events'], RunTransition | undefined]> = {
      progress: [[workerEvent('turn.started', { turn: 1 })], undefined],
      ask: [[requested], undefined],
      suspend_approval: [
        [requested],
        { status: 'waiting_approval', detail: { kind: 'suspend', reason: 'approval', interactionId: ix } },
      ],
      suspend_input: [[], { status: 'paused', detail: { kind: 'suspend', reason: 'input' } }],
      yield: [
        [],
        {
          status: 'queued',
          detail: { kind: 'yield', reason: 'llm_backoff', notBefore: new Date(this.h.clock.now()).toISOString() },
        },
      ],
      complete: [consumed, { status: 'completed', outcome: 'answer' }],
      fail: [[], { status: 'failed', errorType: 'worker_failed', detail: { kind: 'fail' } }],
      cancelled: [[], { status: 'cancelled' }],
      recovering: [[], { status: 'recovering' }],
      needs_reconciliation: [[], { status: 'needs_reconciliation' }],
    };
    const [events, transition] = transitions[step];
    const batch: CommitBatch = {
      commandId: this.next('commit'),
      orgId: run.orgId,
      runId: run.runId,
      ...token,
      events,
      legacyEvents: [],
      ...(transition !== undefined ? { transition } : {}),
      outbox: [],
    };
    const result = await outcome(() => this.h.service.commit(batch));
    if ('ok' in result && result.ok) {
      run.lastCommit = batch;
      if (events.some((event) => event.type === 'interaction.requested')) run.interactions += 1;
    }
    return result;
  }

  private async duplicate(run: WorldRun, changed: boolean): Promise<Outcome> {
    const last = run.lastCommit;
    if (last === null) return { skipped: true };
    const retried = changed ? { ...last, events: [...last.events, workerEvent('turn.started', { turn: 9 })] } : last;
    return outcome(async () => {
      const receipt = await this.h.service.commit(retried);
      expect(receipt.replayed, 'a retried commit replays its receipt').to.equal(true);
    });
  }

  private async cancel(run: WorldRun): Promise<Outcome> {
    const stored = await this.h.service.get(run.orgId, run.runId);
    const commandId = this.next('cancel');
    return outcome(() =>
      this.h.service.control({
        commandId,
        dispatchCommandId: `${commandId}:dispatch`,
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: stored?.revision ?? -1,
        action: 'cancel',
      }),
    );
  }

  private async resolve(run: WorldRun, op: Extract<Op, { kind: 'resolve' }>): Promise<Outcome> {
    const earlier = run.resolved.at(-1);
    if (op.retry && earlier !== undefined) {
      // The same decision again (a double click, a retried request) replays; nothing moves.
      return outcome(() => this.resolveOne(run, earlier.interactionId, earlier.commandId, op.approve));
    }
    const stored = await this.h.service.get(run.orgId, run.runId);
    const interactionId = op.open
      ? [...(stored?.openInteractions ?? [])].sort()[0] ?? 'ix-none'
      : earlier?.interactionId ?? 'ix-never-asked';
    const commandId = this.next('resolve');
    const result = await outcome(() => this.resolveOne(run, interactionId, commandId, op.approve));
    if ('ok' in result && result.ok) run.resolved.push({ interactionId, commandId });
    return result;
  }

  private resolveOne(run: WorldRun, interactionId: string, commandId: string, approve: boolean) {
    return new InteractionService(this.h.service, new InMemoryRunWakeup()).resolve({
      orgId: run.orgId,
      runId: run.runId,
      interactionId,
      decision: approve ? 'approved' : 'denied',
      channel: 'web',
      by: 'p_approver',
      commandId,
    });
  }

  async snapshot(): Promise<WorldSnapshot> {
    const runs: RunView[] = [];
    const wakes: number[] = [];
    for (const run of this.runs) {
      const stored = (await this.h.service.get(run.orgId, run.runId)) as HarnessRun;
      runs.push({
        runId: run.runId,
        status: stored.status,
        fence: stored.fence,
        attempts: stored.attempts ?? 0,
        open: [...(stored.openInteractions ?? [])].sort(),
        deadline: stored.absoluteDeadlineAt,
      });
      const dispatches = await this.h.backend.dispatches(run.orgId, run.runId);
      wakes.push(dispatches.filter((d) => d.kind === 'run_ready' || d.kind === 'run_resume').length);
    }
    const active: Array<string | null> = [];
    const journal: string[][] = [];
    for (const sessionId of this.sessions) {
      const orgId = this.h.tenant.orgId;
      active.push((await this.h.service.getActive(orgId, sessionId))?.runId ?? null);
      const events = await this.h.backend.journal.readStream(orgId, sessionId, 0, 10_000);
      const broken = journalHistoryViolations(events);
      expect(broken, describeViolations(broken)).to.deep.equal([]);
      journal.push(events.map((event) => event.type));
    }
    return { runs, active, wakes, journal };
  }
}

interface ModelRun {
  sessionId: number;
  status: RunStatus;
  fence: number;
  attempts: number;
  lease: number | null;
  notBefore: number;
  deadline: number;
  open: string[];
  pending: number;
  claimed: boolean;
  /** Times the run went (back) to the queue: each owes a wake hint. */
  queuedTimes: number;
  interactions: number;
  /** Decisions a person made (a retry of the last one replays it). */
  resolved: number;
}

/** What a refusal may be named; one operation can be refused for more than one reason. */
export type Expected = { ok: true } | { refused: readonly string[] } | { skipped: true };

const refused = (...codes: string[]): Expected => ({ refused: codes });

/** The pure specification. Mutates itself only through `apply`, after the world answered. */
export class RunModel {
  now = START;
  readonly runs: ModelRun[] = [];
  readonly active: Array<number | null> = [];
  private tokens: number[][] = [];

  private run(index: number): { run: ModelRun; at: number } | undefined {
    if (this.runs.length === 0) return undefined;
    const at = index % this.runs.length;
    return { run: this.runs[at]!, at };
  }

  private owns(run: ModelRun, at: number, stale: boolean): boolean {
    const tokens = this.tokens[at] ?? [];
    return (
      !stale &&
      tokens.length > 0 &&
      WORKER_OWNED_RUN_STATUSES.has(run.status) &&
      tokens.at(-1) === run.fence &&
      run.lease !== null &&
      run.lease > this.now
    );
  }

  /** A token that lost the run; past the deadline Mongo names the deadline instead (either is a lost attempt). */
  private stale(run: ModelRun): Expected {
    return run.deadline <= this.now ? refused('STALE_ATTEMPT', 'DEADLINE_EXCEEDED') : refused('STALE_ATTEMPT');
  }

  expected(op: Op, world: RunWorld): Expected {
    switch (op.kind) {
      case 'admit': {
        const activeAt = this.active[op.session] ?? null;
        if (op.session >= this.active.length || activeAt === null) return { ok: true };
        return STEERABLE_RUN_STATUSES.has(this.runs[activeAt]!.status) ? { ok: true } : refused('RUN_ACTIVE');
      }
      case 'advance':
      case 'sweep':
      case 'relay':
        return { ok: true };
      default:
        break;
    }
    const found = this.run(op.run);
    if (found === undefined) return { skipped: true };
    const { run, at } = found;
    const tokens = this.tokens[at] ?? [];
    switch (op.kind) {
      case 'claim': {
        const expiredOwner = run.status === 'running' && run.lease !== null && run.lease <= this.now;
        const due = run.status === 'queued' && run.notBefore <= this.now;
        const late = run.deadline <= this.now;
        // Both reasons may hold; the adapters name either first.
        if (!due && !expiredOwner) return late ? refused('RUN_NOT_CLAIMABLE', 'DEADLINE_EXCEEDED') : refused('RUN_NOT_CLAIMABLE');
        if (late) return refused('DEADLINE_EXCEEDED');
        return { ok: true };
      }
      case 'renew':
        if (tokens.length < (op.stale ? 2 : 1)) return { skipped: true };
        return this.owns(run, at, op.stale) ? { ok: true } : this.stale(run);
      case 'release':
        if (tokens.length === 0) return { skipped: true };
        if (!this.owns(run, at, false)) return this.stale(run);
        return run.status === 'running' ? { ok: true } : refused('INVALID_TRANSITION');
      case 'commit': {
        if (tokens.length < (op.stale ? 2 : 1)) return { skipped: true };
        // The transition is judged against the run as it is, before the attempt's ownership.
        const rule = this.commitRule(run, op.step);
        if ('refused' in rule) return rule;
        return this.owns(run, at, op.stale) ? { ok: true } : this.stale(run);
      }
      case 'duplicate':
        if (world.run(op.run)?.lastCommit == null) return { skipped: true };
        return op.changed ? refused('IDEMPOTENCY_CONFLICT') : { ok: true };
      case 'cancel':
        return TERMINAL_RUN_STATUSES.has(run.status) ? refused('INVALID_TRANSITION') : { ok: true };
      case 'resolve':
        return this.resolveRule(run, op);
      default:
        return { skipped: true };
    }
  }

  private commitRule(run: ModelRun, step: CommitStep): Expected {
    const status = TRANSITION_TO[step];
    if (status === null) return { ok: true };
    return canWorkerTransition(run.status, status) && V2_STATUSES.has(status)
      ? { ok: true }
      : refused('INVALID_TRANSITION');
  }

  private resolveRule(run: ModelRun, op: Extract<Op, { kind: 'resolve' }>): Expected {
    if (op.retry && run.resolved > 0) return { ok: true };
    // Either reason may be named first when both hold.
    const reasons = [
      ...(run.status === 'cancelling' ? ['RUN_CANCELLING'] : []),
      ...(TERMINAL_RUN_STATUSES.has(run.status) || !op.open || run.open.length === 0 ? ['INTERACTION_NOT_OPEN'] : []),
    ];
    if (reasons.length > 0) return refused(...reasons);
    if (RESUMABLE_RUN_STATUSES.has(run.status) && run.deadline <= this.now) return refused('DEADLINE_EXCEEDED');
    return { ok: true };
  }

  /** The state after `op` answered as expected. */
  apply(op: Op): void {
    switch (op.kind) {
      case 'admit':
        this.admit(op.session);
        return;
      case 'advance':
        this.now += op.ms;
        return;
      case 'sweep':
        this.sweep();
        return;
      case 'relay':
        return;
      default:
        break;
    }
    const { run, at } = this.run(op.run)!;
    switch (op.kind) {
      case 'claim':
        run.fence += 1;
        run.attempts += 1;
        run.status = 'running';
        run.claimed = true;
        run.lease = Math.min(this.now + LEASE_MS, run.deadline);
        (this.tokens[at] ??= []).push(run.fence);
        return;
      case 'renew':
        run.lease = Math.min(this.now + LEASE_MS, run.deadline);
        return;
      case 'release':
        run.status = 'queued';
        run.lease = null;
        run.notBefore = this.now;
        run.attempts = Math.max(0, run.attempts - 1);
        run.queuedTimes += 1;
        return;
      case 'commit':
        this.commit(run, op.step);
        return;
      case 'cancel':
        if (WORKER_OWNED_RUN_STATUSES.has(run.status)) {
          run.status = 'cancelling';
        } else {
          this.end(run, 'cancelled');
        }
        return;
      case 'resolve': {
        if (op.retry && run.resolved > 0) return;
        run.resolved += 1;
        run.open = run.open.slice(1);
        if (RESUMABLE_RUN_STATUSES.has(run.status)) {
          run.status = 'queued';
          run.lease = null;
          run.notBefore = this.now;
          run.queuedTimes += 1;
        }
        return;
      }
      default:
        return;
    }
  }

  private admit(session: number): void {
    const activeAt = this.active[session] ?? null;
    if (activeAt !== null && session < this.active.length) {
      this.runs[activeAt]!.pending += 1;
      return;
    }
    const sessionId = session < this.active.length ? session : this.active.length;
    this.runs.push({
      sessionId,
      status: 'queued',
      fence: 0,
      attempts: 0,
      lease: null,
      notBefore: this.now,
      deadline: this.now + DEADLINE_MS,
      open: [],
      pending: 0,
      claimed: false,
      queuedTimes: 1,
      interactions: 0,
      resolved: 0,
    });
    this.tokens.push([]);
    this.active[sessionId] = this.runs.length - 1;
  }

  private commit(run: ModelRun, step: CommitStep): void {
    const ix = `ix-${String(run.interactions + 1)}`;
    if (step === 'ask' || step === 'suspend_approval') {
      run.interactions += 1;
      if (!run.open.includes(ix)) run.open = [...run.open, ix].sort();
    }
    const leave = (status: RunStatus): void => {
      run.status = status;
      run.lease = null;
    };
    switch (step) {
      case 'suspend_approval':
        leave('waiting_approval');
        run.attempts = 0;
        return;
      case 'suspend_input':
        leave('paused');
        run.attempts = 0;
        return;
      case 'yield':
        leave('queued');
        run.attempts = 0;
        run.notBefore = this.now;
        run.queuedTimes += 1;
        return;
      case 'complete':
        run.pending = 0;
        this.end(run, 'completed');
        return;
      case 'fail':
        this.end(run, 'failed');
        return;
      case 'cancelled':
        this.end(run, 'cancelled');
        return;
      default:
        return;
    }
  }

  private end(run: ModelRun, status: RunStatus): void {
    run.status = status;
    run.lease = null;
    const at = this.runs.indexOf(run);
    if (this.active[run.sessionId] === at) this.active[run.sessionId] = null;
  }

  private sweep(): void {
    for (const run of this.runs) {
      if (WORKER_OWNED_RUN_STATUSES.has(run.status) && run.lease !== null && run.lease <= this.now) {
        if (run.status === 'cancelling') this.end(run, 'cancelled');
        else if (run.deadline <= this.now) this.end(run, 'failed');
        else {
          run.status = 'queued';
          run.lease = null;
          run.notBefore = this.now;
          run.queuedTimes += 1;
        }
      } else if (IDLE.has(run.status) && run.deadline <= this.now) {
        this.end(run, 'failed');
      }
    }
  }

  /** The store agrees with the specification, and the run invariants hold. */
  agree(snapshot: WorldSnapshot): void {
    expect(snapshot.runs.map((r) => [r.status, r.fence, r.attempts, r.open])).to.deep.equal(
      this.runs.map((r) => [r.status, r.fence, r.attempts, r.open]),
    );
    expect(snapshot.active.map((runId) => (runId === null ? null : snapshot.runs.findIndex((r) => r.runId === runId)))).to.deep.equal(
      this.active.map((at) => at ?? null),
    );
    snapshot.runs.forEach((run, at) => {
      expect(Number.isFinite(run.deadline), 'every run has a deadline').to.equal(true);
      expect(snapshot.wakes[at]!, `${run.runId}: a wake hint for each return to the queue`).to.be.at.least(
        this.runs[at]!.queuedTimes,
      );
    });
  }
}

/** Whether `answered` is what `expected` allows. */
export function expectAnswer(expected: Expected, answered: Outcome, label: string): void {
  if ('skipped' in expected) {
    expect(answered, label).to.deep.equal({ skipped: true });
  } else if ('refused' in expected) {
    expect(answered, label).to.have.property('ok', false);
    expect(expected.refused, `${label} refused as ${JSON.stringify(answered)}`).to.include(
      (answered as { code: string }).code,
    );
  } else {
    expect(answered, label).to.deep.equal({ ok: true });
  }
}

/** One operation on the world, checked against the model: the answer, then the state. Returns what was expected. */
export async function step(model: RunModel, world: RunWorld, op: Op): Promise<Expected> {
  const expected = model.expected(op, world);
  expectAnswer(expected, await world.apply(op), JSON.stringify(op));
  if ('ok' in expected) model.apply(op);
  model.agree(await world.snapshot());
  return expected;
}
