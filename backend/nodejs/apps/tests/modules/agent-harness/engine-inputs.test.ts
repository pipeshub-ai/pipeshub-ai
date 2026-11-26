/**
 * E1b IN-8, IN-11: a message names conversation artifacts for an engine run
 * (`inputArtifacts`). Admission keeps them in the run's input and requires the
 * `engine_inputs` worker feature; a loop run and a message steered into a
 * running run refuse them rather than drop them.
 */
import { testModelAccess } from './support/model-access';
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import { profileExecutor } from '../../../src/modules/agent-harness/domain/run-spec';
import { steeringRefusal } from '../../../src/modules/agent-harness/domain/steering';
import {
  KNOWN_WORKER_FEATURES,
  admissionRequirements,
} from '../../../src/modules/agent-harness/domain/worker-compat';
import { runInputSchema } from '../../../src/modules/agent-harness/transport/schemas';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const ENGINE_AGENT = { engines: ['opencode'] };
const ARTIFACTS = [{ artifactId: 'art-1' }, { artifactId: 'art-2', version: 3 }];

const input = (extra: Partial<RunInputSnapshot> = {}): RunInputSnapshot => ({
  query: 'edit it',
  recordIds: [],
  filters: {},
  attachments: [],
  model: { chatMode: 'auto' },
  ...extra,
});

describe('E1b: input artifacts on an engine agent\'s turn', () => {
  let now: number;
  let store: InMemoryExecutionStore;
  let engineProfile: unknown;
  let lines: unknown[][];

  const logger: HarnessLogger = {
    debug: () => undefined,
    info: () => undefined,
    warn: (...args) => void lines.push(['warn', ...args]),
    error: (...args) => void lines.push(['error', ...args]),
  };

  const admit = async (extra: Partial<RunInputSnapshot>, sessionId?: string) => {
    const settings = new FakeEngineCeilings();
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => now },
      agentAccess: grantingAccess((agentKey) =>
        agentDefinition({ agentKey, ...(engineProfile !== undefined ? { engineProfile } : {}) }),
      ),
      logger,
      executors: profileExecutor,
      engineProfiles: settings,
      modelAccess: testModelAccess(),
      roles: { roleOf: async () => 'owner' },
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      ...(sessionId !== undefined ? { sessionId } : {}),
      authorization: 'Bearer t',
      idempotencyKey: `k-${String(now++)}`,
      deadlineMs: 60_000,
      input: input(extra),
    });
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    return outcome.admission;
  };

  const refusal = async (extra: Partial<RunInputSnapshot>, sessionId?: string): Promise<unknown> => {
    try {
      await admit(extra, sessionId);
    } catch (error) {
      return error;
    }
    return expect.fail('expected a refusal');
  };

  beforeEach(() => {
    now = 1_000;
    lines = [];
    engineProfile = ENGINE_AGENT;
    store = new InMemoryExecutionStore({ now: () => now });
  });

  it('IN-8: an engine agent\'s turn keeps the artifacts in its input for its code_task child (CTL-6)', async () => {
    const { run } = await admit({ inputArtifacts: ARTIFACTS });
    expect(run.input?.inputArtifacts).to.deep.equal(ARTIFACTS);
    // A loop run that lists them; the engine child that gets them requires engine_inputs.
    expect(run.requires?.features).to.include('code_task_tool');
    expect(run.requires?.features).to.not.include.members(['engine_inputs', 'executor_engine']);
    const plain = await admit({});
    expect(plain.run.requires?.features).to.not.include('engine_inputs');
  });

  it('IN-8: a loop run naming artifacts is refused, not run without them', async () => {
    engineProfile = undefined;
    const error = await refusal({ inputArtifacts: ARTIFACTS });
    expect(error).to.be.instanceOf(HarnessConflictError);
    expect((error as HarnessConflictError).code).to.equal('INPUT_ARTIFACTS_REQUIRE_ENGINE');
    expect(transportError(error)).to.include({ statusCode: 409 });
    expect(lines.some(([level, line]) => level === 'warn' && line === 'admission.refused')).to.equal(true);
  });

  it('IN-8: a message steered into a running run cannot name artifacts', async () => {
    const first = await admit({});
    const error = await refusal({ inputArtifacts: ARTIFACTS }, first.run.sessionId);
    expect(error).to.be.instanceOf(HarnessConflictError);
    expect((error as HarnessConflictError).code).to.equal('RUN_ACTIVE');
    expect((error as Error).message).to.contain('input_artifacts');
    expect(
      steeringRefusal({ harnessMajor: 2, status: 'running' }, input({ inputArtifacts: ARTIFACTS })),
    ).to.equal('input_artifacts');
    expect(steeringRefusal({ harnessMajor: 2, status: 'running' }, input())).to.equal(null);
  });

  it('IN-8: the body takes at most 20 well-formed artifact references', () => {
    const body = (inputArtifacts: unknown) => ({ query: 'q', inputArtifacts });
    expect(runInputSchema.safeParse(body(ARTIFACTS)).success).to.equal(true);
    const many = Array.from({ length: 21 }, (_, i) => ({ artifactId: `a-${String(i)}` }));
    expect(runInputSchema.safeParse(body(many)).success).to.equal(false);
    expect(runInputSchema.safeParse(body([{ artifactId: 'a', version: 0 }])).success).to.equal(false);
    expect(runInputSchema.safeParse(body([{ artifactId: '' }])).success).to.equal(false);
    expect(runInputSchema.safeParse(body([{ artifactId: 'a', path: '/etc' }])).success).to.equal(false);
  });

  it('IN-11: engine_inputs is a known worker feature, required only by runs naming artifacts', () => {
    expect(KNOWN_WORKER_FEATURES).to.include('engine_inputs');
    const needs = admissionRequirements({ input: input({ inputArtifacts: ARTIFACTS }) }, [], 'engine');
    expect(needs.features).to.include('engine_inputs');
    expect(admissionRequirements({ input: input() }, [], 'engine').features).to.not.include('engine_inputs');
  });
});
