/**
 * SL-05 (C2.02): the run state machine as a table. The durable unit is the run
 * and the conversation is the session (WP-01 ADR), so the table is over the
 * run's statuses. Every cell names what the operation must answer from that
 * status: accepted (and the status it leaves), or refused with a typed code
 * and nothing changed — the run's status, fence, attempts, open interactions
 * and the session's journal. The expectations are written out here, not
 * derived from the code under test; `RunWorld` drives a real
 * `ExecutionService` over the in-memory contract backend.
 */
import { expect } from 'chai';
import {
  canWorkerTransition,
  RUN_STATUSES,
  RunStatus,
} from '../../../src/modules/agent-harness/domain/execution';
import { createContractHarness, contractClock } from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { CommitStep, FIRST_RUN, Op, Outcome, RunWorld, WorldSnapshot } from './support/run-machine';

/** Every worker-written status change, from → allowed targets (the spec's matrix, typed out). */
const WORKER_MATRIX: Record<RunStatus, readonly RunStatus[]> = {
  queued: [],
  running: [
    'queued',
    'running',
    'waiting_approval',
    'paused',
    'recovering',
    'needs_reconciliation',
    'cancelling',
    'completed',
    'failed',
    'cancelled',
  ],
  waiting_approval: ['queued', 'cancelled'],
  paused: ['queued', 'cancelled'],
  recovering: ['queued', 'needs_reconciliation', 'failed', 'cancelled'],
  needs_reconciliation: ['queued', 'failed', 'cancelled'],
  cancelling: ['cancelled', 'failed'],
  completed: [],
  failed: [],
  cancelled: [],
};

describe('SL-05 the worker transition matrix (every from × to pair)', () => {
  it('covers every status', () => {
    expect(Object.keys(WORKER_MATRIX).sort()).to.deep.equal([...RUN_STATUSES].sort());
  });

  for (const from of RUN_STATUSES) {
    for (const to of RUN_STATUSES) {
      const legal = WORKER_MATRIX[from].includes(to);
      it(`SL-05 ${from} → ${to} is ${legal ? 'legal' : 'illegal'}`, () => {
        expect(canWorkerTransition(from, to)).to.equal(legal);
      });
    }
  }

  it('SL-05 a terminal status is absorbing', () => {
    for (const status of ['completed', 'failed', 'cancelled'] as const) {
      expect(RUN_STATUSES.filter((to) => canWorkerTransition(status, to))).to.deep.equal([]);
    }
  });
});

type Start =
  | 'queued'
  | 'running'
  | 'waiting_approval'
  | 'paused'
  | 'cancelling'
  | 'completed'
  | 'failed'
  | 'cancelled';

/** How each status is reached from a fresh admitted run. Every start but none holds an attempt token, so a
 *  worker operation is judged rather than skipped. */
const commit = (step: CommitStep): Op => ({ kind: 'commit', run: 0, step, stale: false });
const CLAIM: Op = { kind: 'claim', run: 0, second: false };
const SETUP: Record<Start, Op[]> = {
  queued: [CLAIM, commit('yield')],
  running: [CLAIM],
  waiting_approval: [CLAIM, commit('suspend_approval')],
  paused: [CLAIM, commit('suspend_input')],
  cancelling: [CLAIM, { kind: 'cancel', run: 0 }],
  completed: [CLAIM, commit('complete')],
  failed: [CLAIM, commit('fail')],
  cancelled: [CLAIM, commit('yield'), { kind: 'cancel', run: 0 }],
};

type OpName =
  | 'claim'
  | 'renew'
  | 'release'
  | `commit:${CommitStep}`
  | 'cancel'
  | 'approve'
  | 'approve_not_asked'
  | 'message';

const OPS: Record<OpName, Op> = {
  claim: CLAIM,
  renew: { kind: 'renew', run: 0, stale: false },
  release: { kind: 'release', run: 0 },
  'commit:progress': commit('progress'),
  'commit:suspend_approval': commit('suspend_approval'),
  'commit:suspend_input': commit('suspend_input'),
  'commit:yield': commit('yield'),
  'commit:complete': commit('complete'),
  'commit:fail': commit('fail'),
  'commit:cancelled': commit('cancelled'),
  'commit:recovering': commit('recovering'),
  'commit:needs_reconciliation': commit('needs_reconciliation'),
  'commit:ask': commit('ask'),
  cancel: { kind: 'cancel', run: 0 },
  approve: { kind: 'resolve', run: 0, open: true, approve: true, retry: false },
  approve_not_asked: { kind: 'resolve', run: 0, open: false, approve: true, retry: false },
  // A person's message to the run's session: it joins the run, or starts the next one once this one ended.
  message: { kind: 'admit', session: 0 },
};

/** Accepted, leaving the run in that status; or refused with one of these codes. */
type Cell = RunStatus | { refused: readonly string[] };
const no = (...codes: string[]): Cell => ({ refused: codes });
const INVALID = no('INVALID_TRANSITION');
const STALE = no('STALE_ATTEMPT');
const NOT_CLAIMABLE = no('RUN_NOT_CLAIMABLE');
const NOT_OPEN = no('INTERACTION_NOT_OPEN');

/** A worker commit from a status that does not own the run: the transition is judged first. */
function idleCommits(legal: readonly RunStatus[]): Record<`commit:${CommitStep}`, Cell> {
  const judged = (to: RunStatus): Cell => (legal.includes(to) ? STALE : INVALID);
  return {
    'commit:progress': STALE,
    'commit:ask': STALE,
    'commit:suspend_approval': judged('waiting_approval'),
    'commit:suspend_input': judged('paused'),
    'commit:yield': judged('queued'),
    'commit:complete': judged('completed'),
    'commit:fail': judged('failed'),
    'commit:cancelled': judged('cancelled'),
    'commit:recovering': INVALID,
    'commit:needs_reconciliation': INVALID,
  };
}

const TERMINAL_ROW = (status: RunStatus): Record<OpName, Cell> => ({
  claim: NOT_CLAIMABLE,
  renew: STALE,
  release: STALE,
  ...idleCommits([]),
  cancel: INVALID,
  approve: NOT_OPEN,
  approve_not_asked: NOT_OPEN,
  message: status,
});

const TABLE: Record<Start, Record<OpName, Cell>> = {
  queued: {
    claim: 'running',
    renew: STALE,
    release: STALE,
    ...idleCommits([]),
    cancel: 'cancelled',
    approve: NOT_OPEN,
    approve_not_asked: NOT_OPEN,
    message: 'queued',
  },
  running: {
    claim: NOT_CLAIMABLE,
    renew: 'running',
    release: 'queued',
    'commit:progress': 'running',
    'commit:ask': 'running',
    'commit:suspend_approval': 'waiting_approval',
    'commit:suspend_input': 'paused',
    'commit:yield': 'queued',
    'commit:complete': 'completed',
    'commit:fail': 'failed',
    'commit:cancelled': 'cancelled',
    // Statuses of the v1 store a v2 run never enters: the journal has no event for them.
    'commit:recovering': INVALID,
    'commit:needs_reconciliation': INVALID,
    cancel: 'cancelling',
    approve: NOT_OPEN,
    approve_not_asked: NOT_OPEN,
    message: 'running',
  },
  waiting_approval: {
    claim: NOT_CLAIMABLE,
    renew: STALE,
    release: STALE,
    ...idleCommits(['queued', 'cancelled']),
    cancel: 'cancelled',
    approve: 'queued',
    approve_not_asked: NOT_OPEN,
    message: 'waiting_approval',
  },
  paused: {
    claim: NOT_CLAIMABLE,
    renew: STALE,
    release: STALE,
    ...idleCommits(['queued', 'cancelled']),
    cancel: 'cancelled',
    approve: NOT_OPEN,
    approve_not_asked: NOT_OPEN,
    message: 'paused',
  },
  cancelling: {
    claim: NOT_CLAIMABLE,
    renew: 'cancelling',
    release: INVALID,
    'commit:progress': 'cancelling',
    'commit:ask': 'cancelling',
    'commit:suspend_approval': INVALID,
    'commit:suspend_input': INVALID,
    'commit:yield': INVALID,
    'commit:complete': INVALID,
    'commit:fail': 'failed',
    'commit:cancelled': 'cancelled',
    'commit:recovering': INVALID,
    'commit:needs_reconciliation': INVALID,
    cancel: 'cancelling',
    approve: no('RUN_CANCELLING', 'INTERACTION_NOT_OPEN'),
    approve_not_asked: no('RUN_CANCELLING', 'INTERACTION_NOT_OPEN'),
    message: no('RUN_ACTIVE'),
  },
  completed: TERMINAL_ROW('completed'),
  failed: TERMINAL_ROW('failed'),
  cancelled: TERMINAL_ROW('cancelled'),
};

const unchanged = (snapshot: WorldSnapshot) => ({
  run: snapshot.runs[0] && {
    status: snapshot.runs[0].status,
    fence: snapshot.runs[0].fence,
    attempts: snapshot.runs[0].attempts,
    open: snapshot.runs[0].open,
  },
  journal: snapshot.journal,
});

async function worldAt(start: Start): Promise<RunWorld> {
  const clock = contractClock();
  const world = new RunWorld(createContractHarness(inMemoryBackend(clock), clock));
  for (const op of [FIRST_RUN, ...SETUP[start]]) {
    const answered = await world.apply(op);
    expect(answered, `set-up ${JSON.stringify(op)} for ${start}`).to.deep.equal({ ok: true });
  }
  expect((await world.snapshot()).runs[0]!.status).to.equal(start);
  return world;
}

describe('SL-05 every operation from every run status (ExecutionService, in-memory store)', function () {
  this.timeout(60_000);

  for (const start of Object.keys(TABLE) as Start[]) {
    for (const name of Object.keys(OPS) as OpName[]) {
      const cell = TABLE[start][name];
      const label = typeof cell === 'string' ? `accepted → ${cell}` : `refused ${cell.refused.join('|')}`;
      it(`SL-05 ${start} + ${name}: ${label}`, async () => {
        const world = await worldAt(start);
        const before = await world.snapshot();
        const answered: Outcome = await world.apply(OPS[name]);
        const after = await world.snapshot();
        if (typeof cell === 'string') {
          expect(answered).to.deep.equal({ ok: true });
          expect(after.runs[0]!.status).to.equal(cell);
        } else {
          expect(answered).to.have.property('ok', false);
          expect(cell.refused).to.include((answered as { code: string }).code);
          expect(unchanged(after)).to.deep.equal(unchanged(before));
        }
      });
    }
  }
});
