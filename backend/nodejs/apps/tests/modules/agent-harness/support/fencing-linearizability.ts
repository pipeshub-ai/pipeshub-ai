/**
 * J-10: two fenced workers race on one run (claim, renew, journal commit,
 * commit retry, time passing). Operations run in rounds; the two operations
 * of a concurrent round run at the same time. After each round the observed
 * outcomes and the whole journal must equal what a sequential reference
 * model produces for SOME order of that round's operations (a linearization
 * point per operation). Any adapter that accepts a stale fence, loses or
 * duplicates an append, or leaves a seq gap fails with a shrunk history.
 */
import { expect } from 'chai';
import fc from 'fast-check';
import { Types } from 'mongoose';
import { HarnessConflictError } from '../../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { ContractHarness } from './execution-store-contract';
import { propertyRuns } from '../../../support/harness/property-profile';
import { turnStarted } from './journal-fixtures';

type Worker = 'a' | 'b';
const LEASE_MS = 1_000;

export type Op =
  | { kind: 'claim'; worker: Worker }
  | { kind: 'renew'; worker: Worker }
  | { kind: 'commit'; worker: Worker; size: number }
  | { kind: 'retry'; worker: Worker }
  | { kind: 'advance'; ms: number };

/** A round is one op, or one op per worker issued concurrently. */
export type Round = Op[];

type Outcome =
  | { ok: true; range?: [number, number] }
  | { ok: false; code: string };

/** A commit as the worker issued it; a retry re-sends exactly this. */
interface IssuedCommit {
  commandId: string;
  size: number;
  turn: number;
  attemptId: string | null;
  fence: number;
}

interface WorkerView {
  attemptId: string | null;
  fence: number;
  lastCommand: IssuedCommit | null;
}

interface JournalEntry {
  fence: number;
  attemptId: string;
  turn: number;
}

/** The sequential specification the stores must be linearizable to. */
interface ModelState {
  now: number;
  status: 'queued' | 'running';
  fence: number;
  owner: Worker | null;
  attemptId: string | null;
  leaseExpiresAt: number;
  journal: JournalEntry[];
  receipts: Record<string, [number, number]>;
  workers: Record<Worker, WorkerView>;
  claims: number;
}

const initialModel = (now: number): ModelState => ({
  now,
  status: 'queued',
  fence: 0,
  owner: null,
  attemptId: null,
  leaseExpiresAt: 0,
  journal: [],
  receipts: {},
  workers: {
    a: { attemptId: null, fence: 0, lastCommand: null },
    b: { attemptId: null, fence: 0, lastCommand: null },
  },
  claims: 0,
});

/** Ids an op uses are fixed when the round is planned, not per ordering. */
interface PlannedOp {
  op: Op;
  /** For a claim: the attempt id it will be granted. */
  attemptId: string;
  /** For a commit: the command as issued, with the worker's view at issue. */
  commit?: IssuedCommit;
}

const owns = (
  state: ModelState,
  worker: Worker,
  attemptId: string | null,
  fence: number,
): boolean =>
  state.status === 'running' &&
  state.owner === worker &&
  attemptId !== null &&
  state.attemptId === attemptId &&
  state.fence === fence &&
  state.leaseExpiresAt > state.now;

function commitModel(
  state: ModelState,
  worker: Worker,
  command: IssuedCommit,
): Outcome {
  const stored = state.receipts[command.commandId];
  if (stored) return { ok: true, range: stored };
  if (!owns(state, worker, command.attemptId, command.fence)) {
    return { ok: false, code: 'STALE_ATTEMPT' };
  }
  const firstSeq = state.journal.length + 1;
  for (let index = 0; index < command.size; index += 1) {
    state.journal.push({
      fence: state.fence,
      attemptId: state.attemptId as string,
      turn: command.turn,
    });
  }
  const range: [number, number] = [firstSeq, state.journal.length];
  state.receipts[command.commandId] = range;
  return { ok: true, range };
}

export function applyModel(state: ModelState, planned: PlannedOp): Outcome {
  const { op } = planned;
  switch (op.kind) {
    case 'advance':
      state.now += op.ms;
      return { ok: true };
    case 'claim': {
      const claimable =
        state.status === 'queued' ||
        (state.status === 'running' && state.leaseExpiresAt <= state.now);
      if (!claimable) return { ok: false, code: 'RUN_NOT_CLAIMABLE' };
      state.status = 'running';
      state.fence += 1;
      state.owner = op.worker;
      state.attemptId = planned.attemptId;
      state.leaseExpiresAt = state.now + LEASE_MS;
      state.workers[op.worker].attemptId = planned.attemptId;
      state.workers[op.worker].fence = state.fence;
      return { ok: true };
    }
    case 'renew': {
      const view = state.workers[op.worker];
      if (!owns(state, op.worker, view.attemptId, view.fence)) {
        return { ok: false, code: 'STALE_ATTEMPT' };
      }
      state.leaseExpiresAt = state.now + LEASE_MS;
      return { ok: true };
    }
    case 'commit': {
      const command = planned.commit as IssuedCommit;
      state.workers[op.worker].lastCommand = command;
      return commitModel(state, op.worker, command);
    }
    case 'retry': {
      const last = state.workers[op.worker].lastCommand;
      if (last === null) return { ok: true };
      return commitModel(state, op.worker, last);
    }
  }
}

const cloneModel = (state: ModelState): ModelState => structuredClone(state);

const permutations = <T>(items: T[]): T[][] =>
  items.length <= 1
    ? [items]
    : items.flatMap((item, index) =>
        permutations([...items.slice(0, index), ...items.slice(index + 1)]).map(
          (rest) => [item, ...rest],
        ),
      );

const workerOp = (worker: Worker): fc.Arbitrary<Op> =>
  fc.oneof(
    { weight: 3, arbitrary: fc.constant<Op>({ kind: 'claim', worker }) },
    { weight: 1, arbitrary: fc.constant<Op>({ kind: 'renew', worker }) },
    {
      weight: 4,
      arbitrary: fc
        .integer({ min: 1, max: 3 })
        .map((size): Op => ({ kind: 'commit', worker, size })),
    },
    { weight: 1, arbitrary: fc.constant<Op>({ kind: 'retry', worker }) },
  );

export const roundsArbitrary = (concurrent: boolean): fc.Arbitrary<Round[]> => {
  const single = fc.oneof(
    workerOp('a').map((op) => [op]),
    workerOp('b').map((op) => [op]),
    fc
      .constantFrom(250, 600, 1_100)
      .map((ms): Round => [{ kind: 'advance', ms }]),
  );
  const round = concurrent
    ? fc.oneof(
        { weight: 2, arbitrary: single },
        {
          weight: 3,
          arbitrary: fc
            .tuple(workerOp('a'), workerOp('b'))
            .map(([a, b]): Round => [a, b]),
        },
      )
    : single;
  return fc.array(round, { minLength: 1, maxLength: 14 });
};

export interface LinearizabilityTarget {
  harness: ContractHarness;
  setNow(value: number): void;
  now(): number;
}

const codeOf = (error: unknown): string => {
  if (error instanceof HarnessConflictError) return error.code;
  throw error;
};

/** What the generated histories exercised, so a vacuous run is visible. */
export interface HistoryStats {
  acceptedCommits: number;
  staleCommits: number;
  concurrentRounds: number;
}

/** Runs one history against the store and checks it round by round. */
export async function checkHistory(
  target: LinearizabilityTarget,
  rounds: Round[],
  stats: HistoryStats,
): Promise<void> {
  const { harness: h } = target;
  const start = 1_000;
  target.setNow(start);
  const tenant = {
    orgId: new Types.ObjectId().toString(),
    actorId: new Types.ObjectId().toString(),
  };
  const admitted = await h.admit({ ...tenant, deadlineMs: 24 * 3_600_000 });
  const run: HarnessRun = admitted.run;
  let model = initialModel(start);
  const views: Record<Worker, WorkerView> = structuredClone(model.workers);
  let serial = 0;

  const execute = async (planned: PlannedOp): Promise<Outcome> => {
    const { op } = planned;
    try {
      switch (op.kind) {
        case 'advance':
          target.setNow(target.now() + op.ms);
          return { ok: true };
        case 'claim': {
          const claimed = await h.service.claim({
            orgId: run.orgId,
            runId: run.runId,
            workerId: op.worker,
            attemptId: planned.attemptId,
            leaseDurationMs: LEASE_MS,
          });
          views[op.worker].attemptId = claimed.run.attemptId;
          views[op.worker].fence = claimed.run.fence;
          return { ok: true };
        }
        case 'renew': {
          const view = views[op.worker];
          if (view.attemptId === null) {
            return { ok: false, code: 'STALE_ATTEMPT' };
          }
          await h.service.renew({
            orgId: run.orgId,
            runId: run.runId,
            workerId: op.worker,
            attemptId: view.attemptId,
            fence: view.fence,
            leaseDurationMs: LEASE_MS,
          });
          return { ok: true };
        }
        case 'commit':
        case 'retry': {
          const view = views[op.worker];
          const command =
            op.kind === 'commit'
              ? (planned.commit as IssuedCommit)
              : view.lastCommand;
          if (command === null) return { ok: true };
          view.lastCommand = command;
          if (command.attemptId === null) {
            return { ok: false, code: 'STALE_ATTEMPT' };
          }
          const receipt = await h.service.commit({
            commandId: command.commandId,
            orgId: run.orgId,
            runId: run.runId,
            workerId: op.worker,
            attemptId: command.attemptId,
            fence: command.fence,
            events: Array.from({ length: command.size }, () =>
              turnStarted(command.turn),
            ),
            legacyEvents: [],
            outbox: [],
          });
          const range = receipt.journal;
          return {
            ok: true,
            range: range ? [range.firstSeq, range.lastSeq] : undefined,
          };
        }
      }
    } catch (error) {
      return { ok: false, code: codeOf(error) };
    }
  };

  for (const [index, round] of rounds.entries()) {
    // Each op sees its worker's view as of the start of the round: a round
    // has at most one op per worker, so nothing else changes it meanwhile.
    const planned: PlannedOp[] = round.map((op) => {
      serial += 1;
      const view = op.kind === 'advance' ? null : views[op.worker];
      return {
        op,
        attemptId: `${op.kind === 'advance' ? 'x' : op.worker}-${String(serial)}`,
        ...(op.kind === 'commit' && view
          ? {
              commit: {
                commandId: `cmd-${String(serial)}`,
                size: op.size,
                turn: serial,
                attemptId: view.attemptId,
                fence: view.fence,
              },
            }
          : {}),
      };
    });
    const observed = await Promise.all(planned.map(execute));
    if (planned.length > 1) stats.concurrentRounds += 1;
    planned.forEach(({ op }, position) => {
      if (op.kind !== 'commit') return;
      const outcome = observed[position] as Outcome;
      if (outcome.ok) stats.acceptedCommits += 1;
      else if (outcome.code === 'STALE_ATTEMPT') stats.staleCommits += 1;
    });
    const stored = await h.backend.journal.readStream(
      run.orgId,
      run.sessionId,
      0,
      10_000,
    );
    expect(
      stored.map((event) => event.seq),
      'gap-free seq',
    ).to.deep.equal(stored.map((_, position) => position + 1));
    const journal = stored.map((event) => ({
      fence: event.fence as number,
      attemptId: event.attemptId as string,
      turn: (event.data as { turn: number }).turn,
    }));
    const explained = permutations(planned.map((_, i) => i)).find((order) => {
      const candidate = cloneModel(model);
      const outcomes: Outcome[] = [];
      for (const position of order) {
        outcomes[position] = applyModel(
          candidate,
          planned[position] as PlannedOp,
        );
      }
      const matches =
        JSON.stringify(outcomes) === JSON.stringify(observed) &&
        JSON.stringify(candidate.journal) === JSON.stringify(journal);
      if (matches) model = candidate;
      return matches;
    });
    if (explained === undefined) {
      expect.fail(
        `round ${String(index)} is not linearizable: ops ${JSON.stringify(
          round,
        )} observed ${JSON.stringify(observed)} journal ${JSON.stringify(
          journal,
        )} model before ${JSON.stringify({
          fence: model.fence,
          owner: model.owner,
          journal: model.journal,
        })}`,
      );
    }
  }

  // Fences never go backwards along the journal.
  const fences = model.journal.map((entry) => entry.fence);
  expect(fences).to.deep.equal([...fences].sort((x, y) => x - y));
}

export async function assertLinearizableFencing(
  target: LinearizabilityTarget,
  options: { concurrent: boolean; numRuns: number },
): Promise<HistoryStats> {
  const stats: HistoryStats = {
    acceptedCommits: 0,
    staleCommits: 0,
    concurrentRounds: 0,
  };
  await fc.assert(
    fc.asyncProperty(roundsArbitrary(options.concurrent), (rounds) =>
      checkHistory(target, rounds, stats),
    ),
    // Seeded like every harness property (FC_SEED replays a printed failure): unseeded, which commits reached
    // the store's lapsed-lease refusal changed from run to run, and the coverage gate's figure with it.
    propertyRuns(options.numRuns, options.numRuns),
  );
  expect(stats.acceptedCommits, 'accepted commits').to.be.greaterThan(0);
  expect(stats.staleCommits, 'fenced-out commits').to.be.greaterThan(0);
  if (options.concurrent) {
    expect(stats.concurrentRounds, 'concurrent rounds').to.be.greaterThan(0);
  }
  return stats;
}
