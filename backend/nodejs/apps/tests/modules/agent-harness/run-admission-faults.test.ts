/**
 * Admission's fault paths (RunAdmissionService over the in-memory store): what a store or a setting that cannot
 * be read does to a turn. Each either refuses with a code the chat route maps to a retry, or falls back to the
 * stricter answer, and never lets a turn through unchecked:
 * - the org's placement refused (OWD-12) refuses the turn (`run_placement_refused`);
 * - answers without a session, or for a run this session does not hold, are not found (WP-64);
 * - whether a message answers a question that cannot be read refuses the turn as unavailable;
 * - the collaborator-turns setting that cannot be read reads as off (the collaborator is refused);
 * - the labelled turns that cannot be read list none (P2.5a), and the turn is still admitted;
 * - a log line that throws never fails an admission that succeeded.
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import {
  RunAdmissionService,
  type RunAdmissionServiceOptions,
  type SubmitDurableRun,
} from '../../../src/modules/agent-harness/application/run-admission-service';
import type { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { HARNESS_ENABLED } from './support/rollout';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };

interface Logged {
  level: string;
  event: string;
  meta: Record<string, unknown>;
}

function world(overrides: Partial<RunAdmissionServiceOptions> = {}, opts: { throwingInfo?: boolean } = {}) {
  let now = 1_000;
  const store = new InMemoryExecutionStore({ now: () => now });
  const logged: Logged[] = [];
  const at = (level: string) => (event: string, meta?: Record<string, unknown>) => {
    if (level === 'info' && opts.throwingInfo === true) throw new Error('log sink down');
    logged.push({ level, event, meta: meta ?? {} });
  };
  const logger: HarnessLogger = { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') };
  const service = new RunAdmissionService({
    store,
    rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
    clock: { now: () => now },
    agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey })),
    logger,
    ...overrides,
  });
  const submit = (request: Partial<SubmitDurableRun> = {}) =>
    service.submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${String(now++)}`,
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
      ...request,
    });
  return { store, service, submit, logged };
}

async function rejection(work: Promise<unknown>): Promise<Error> {
  try {
    await work;
  } catch (error) {
    return error as Error;
  }
  throw new Error('expected a refusal');
}

describe('admission faults: a store or setting that cannot be read', () => {
  it('a placement the org may not have here refuses the turn with the placement’s own error', async () => {
    const w = world({
      placement: {
        place: async () => {
          throw new HarnessConflictError('RUN_PLACEMENT_REFUSED', "The org's runs are placed elsewhere");
        },
      },
    });
    const error = await rejection(w.submit());
    expect(error).to.be.instanceOf(HarnessConflictError);
    expect((error as HarnessConflictError).code).to.equal('RUN_PLACEMENT_REFUSED');
    const refused = w.logged.find((line) => line.event === 'admission.refused');
    expect(refused?.meta).to.include({ reason: 'run_placement_refused', errorType: 'HarnessConflictError' });
  });

  it('a placement that answers lets the turn through', async () => {
    const placed: string[] = [];
    const w = world({ placement: { place: async (orgId) => (placed.push(orgId), 'eu-west-1') } });
    expect((await w.submit()).route).to.equal('durable');
    expect(placed).to.deep.equal(['org-1']);
  });

  it('answers sent without a chat are not found', async () => {
    const w = world();
    const error = await rejection(w.submit({ answers: 'run-x' } as Partial<SubmitDurableRun>));
    expect(error.name).to.equal('HarnessNotFoundError');
  });

  it('answers for a run this chat does not hold are not found', async () => {
    const w = world();
    const first = await w.submit();
    if (first.route !== 'durable') return expect.fail('expected a durable run');
    const error = await rejection(
      w.submit({ sessionId: first.admission.run.sessionId, answers: 'run-elsewhere' } as Partial<SubmitDurableRun>),
    );
    expect(error.name).to.equal('HarnessNotFoundError');
  });

  it('whether the message answers a question that cannot be read refuses the turn as unavailable, with a log line', async () => {
    const w = world();
    const first = await w.submit();
    if (first.route !== 'durable') return expect.fail('expected a durable run');
    w.store.answeredRun = async () => {
      throw new TypeError('store down');
    };
    const error = await rejection(w.submit({ sessionId: first.admission.run.sessionId }));
    expect(error.name).to.equal('HarnessRunConfigUnavailableError');
    expect(error.message).to.match(/could not be checked/);
    const line = w.logged.find((entry) => entry.event === 'run.answered_run_unreadable');
    expect(line?.meta).to.include({ orgId: 'org-1', sessionId: first.admission.run.sessionId, errorType: 'TypeError' });
  });

  it('a collaborator’s turn while the collaborator-turns setting cannot be read is refused, never let through', async () => {
    const owner = world({
      roles: { roleOf: async () => 'collaborator' },
      collaboratorTurns: {
        enabled: async () => {
          throw new Error('settings down');
        },
      },
    });
    const first = await owner.submit();
    if (first.route !== 'durable') return expect.fail('expected a durable run');
    const error = await rejection(
      owner.submit({ actorId: 'user-2', sessionId: first.admission.run.sessionId }),
    );
    expect(error.name).to.equal('HarnessSessionRoleRefusedError');
    // The same refusal as a deployment with the setting off: unreadable reads as off.
    const off = world({ roles: { roleOf: async () => 'collaborator' }, collaboratorTurns: { enabled: async () => false } });
    const seeded = await off.submit();
    if (seeded.route !== 'durable') return expect.fail('expected a durable run');
    const offError = await rejection(off.submit({ actorId: 'user-2', sessionId: seeded.admission.run.sessionId }));
    expect(offError.name).to.equal(error.name);
    expect(offError.message).to.equal(error.message);
  });

  it('labelled turns that cannot be read list none, and the turn is still admitted', async () => {
    const passing: unknown[] = [];
    const w = world({
      turnLabels: {
        passingTurns: async (...args: unknown[]) => {
          passing.push(args);
          return [];
        },
      } as never,
    });
    const first = await w.submit();
    if (first.route !== 'durable') return expect.fail('expected a durable run');
    w.store.labelledTurns = async () => {
      throw new RangeError('index down');
    };
    const outcome = await w.submit({ sessionId: first.admission.run.sessionId });
    expect(outcome.route).to.equal('durable');
    expect(passing).to.deep.equal([]);
    const line = w.logged.find((entry) => entry.event === 'turn_labels.read_failed');
    expect(line?.meta).to.include({ errorType: 'RangeError', choice: 'stub_turns', actorId: 'user-1' });
  });

  it('a log line that throws never fails an admission that succeeded', async () => {
    const w = world({}, { throwingInfo: true });
    const outcome = await w.submit();
    expect(outcome.route).to.equal('durable');
  });
});
