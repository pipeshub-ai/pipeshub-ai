/**
 * UX3 QD-2, QD-3, PS-1: the conversation's own facts route a turn before
 * anything is written: a project's turn and a turn nobody could run go to
 * today's loop; a session already durable stays durable.
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryWorkerFeatureSightings } from '../../../src/modules/agent-harness/adapters/in-memory-worker-feature-sightings';
import {
  RunAdmissionService,
  SubmitOutcome,
} from '../../../src/modules/agent-harness/application/run-admission-service';
import { WorkerFeatureAvailability } from '../../../src/modules/agent-harness/application/worker-feature-availability';
import { SessionPlacement } from '../../../src/modules/agent-harness/ports/chat-session-repository';
import { fixedRollout } from './support/rollout';

const SESSION = '64b7f0c2a1b2c3d4e5f60718';

const world = (options: { placement?: SessionPlacement | null; workerSeen?: boolean; failPlacement?: boolean }) => {
  const clock = { now: () => 1_000 };
  const liveness = new WorkerFeatureAvailability({ sightings: new InMemoryWorkerFeatureSightings(clock), clock });
  const store = new InMemoryExecutionStore({ now: () => 1_000 });
  const placements: unknown[] = [];
  const service = new RunAdmissionService({
    store,
    rollout: fixedRollout(),
    clock,
    sessions: {
      placement: async (scope) => {
        placements.push(scope);
        if (options.failPlacement === true) throw new Error('mongo down');
        return options.placement ?? null;
      },
    },
    liveness,
  });
  const submit = async (sessionId?: string): Promise<SubmitOutcome> => {
    if (options.workerSeen === true) await liveness.observe(['chat']);
    return service.submit({
      orgId: 'org-1',
      actorId: 'user-1',
      ...(sessionId !== undefined ? { sessionId } : {}),
      idempotencyKey: 'k-1',
      firstSubmission: true,
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'internal_search' } },
    });
  };
  return { submit, placements };
};

const reason = (outcome: SubmitOutcome): string =>
  outcome.route === 'legacy' ? outcome.rollout.reason : 'durable';
/** The in-memory store knows no existing session: reaching it is reaching admission. */
const routed = (pending: Promise<SubmitOutcome>): Promise<string> =>
  pending.then(reason, (error: unknown) => (error instanceof Error && error.name === 'HarnessNotFoundError' ? 'admission' : 'error'));

describe('admission routes by the conversation itself (UX3)', () => {
  it('PS-1: a project conversation goes to today’s loop, whatever the browser sent', async () => {
    const w = world({ placement: { inProject: true, durable: false }, workerSeen: true });
    expect(reason(await w.submit(SESSION))).to.equal('project_scope');
    expect(w.placements).to.deep.equal([{ orgId: 'org-1', actorId: 'user-1', sessionId: SESSION }]);
  });

  it('PS-1: a project conversation that once ran durably still goes to today’s loop', async () => {
    const w = world({ placement: { inProject: true, durable: true }, workerSeen: true });
    expect(reason(await w.submit(SESSION))).to.equal('project_scope');
  });

  it('QD-2: no worker seen, a new chat: today’s loop answers at once', async () => {
    expect(reason(await world({ workerSeen: false }).submit())).to.equal('no_live_worker');
  });

  it('QD-2: no worker seen, a conversation that never ran durably: today’s loop', async () => {
    const w = world({ placement: { inProject: false, durable: false }, workerSeen: false });
    expect(reason(await w.submit(SESSION))).to.equal('no_live_worker');
  });

  it('QD-3: no worker seen, a durable session: admitted (its turns stay together)', async () => {
    const w = world({ placement: { inProject: false, durable: true }, workerSeen: false });
    expect(await routed(w.submit(SESSION))).to.equal('admission');
  });

  it('QD-2: a worker seen, a new chat: durable', async () => {
    expect(reason(await world({ workerSeen: true }).submit())).to.equal('durable');
  });

  it('a placement that cannot be read routes nothing to legacy by itself', async () => {
    const w = world({ failPlacement: true, workerSeen: true });
    expect(await routed(w.submit(SESSION))).to.equal('admission');
  });
});
