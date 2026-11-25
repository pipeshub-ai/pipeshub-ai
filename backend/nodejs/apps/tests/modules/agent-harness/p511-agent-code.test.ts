/**
 * P5.11 RC-N1..RC-N5: `run_code` on the durable loop. Node pins `agent-code`
 * only for an agent loop run admitted while a probe proved a code sandbox
 * (worker feature `agent_code`), and only if the capabilities its claim pins
 * still prove one; the sweeper reaps such runs' sandboxes.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { HttpSandboxIsolationProbe } from '../../../src/modules/agent-harness/adapters/http/http-sandbox-isolation-probe';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { reapLabels } from '../../../src/modules/agent-harness/application/run-sweeper';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  capabilitySnapshot,
  CapabilitySnapshot,
  LEAST_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/capabilities';
import {
  childFeatures,
  childGrants,
  childRunSpec,
} from '../../../src/modules/agent-harness/domain/child-runs';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  pinRunSpec,
  sandboxProfileFor,
  SpecSubject,
} from '../../../src/modules/agent-harness/domain/run-spec';
import {
  admissionRequirements,
  KNOWN_WORKER_FEATURES,
} from '../../../src/modules/agent-harness/domain/worker-compat';
import { sandboxIsolationProbes } from '../../../src/modules/agent-harness/composition';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { goldenSpecSubject } from './support/golden-run-spec';
import { HARNESS_ENABLED } from './support/rollout';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const snapshot = (isolation: 'none' | 'container' | 'container_gvisor'): CapabilitySnapshot =>
  capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true, 'sandbox.isolation': isolation }, 1);
const PROVED = snapshot('container');
const UNPROVED = snapshot('none');

const subject = (features: string[] | null): SpecSubject => {
  const golden = goldenSpecSubject();
  return {
    ...golden,
    run: {
      ...golden.run,
      ...(features !== null ? { requires: { foldVersion: 8, features } } : {}),
    } as HarnessRun,
  };
};

describe('P5.11 RC-N1: admission requires agent_code for an agent loop run while a sandbox is proved', () => {
  const admit = async (
    capabilities: CapabilitySnapshot | null,
    agentKey: string | undefined,
    chatMode = 'quick',
  ): Promise<HarnessRun> => {
    const store = new InMemoryExecutionStore({ now: () => 1_000 });
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => 1_000 },
      agentAccess: grantingAccess((key) => agentDefinition({ agentKey: key })),
      capabilities: { current: () => capabilities },
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      ...(agentKey !== undefined ? { agentKey } : {}),
      authorization: 'Bearer t',
      idempotencyKey: `k-${Math.random()}`,
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode } },
    });
    if (outcome.route !== 'durable') expect.fail('expected a durable admission');
    return outcome.admission.run;
  };

  it('an agent loop run with a proved sandbox requires it', async () => {
    expect((await admit(PROVED, 'agent-1')).requires?.features).to.include('agent_code');
  });

  it('no probe yet: pending, required, and the claim decides (ADR-CAP-01 D3)', async () => {
    expect((await admit(null, 'agent-1')).requires?.features).to.include('agent_code');
  });

  it('isolation none, or the assistant: not required', async () => {
    expect((await admit(UNPROVED, 'agent-1')).requires?.features).to.not.include('agent_code');
    const assistant = await admit(PROVED, undefined, 'internal_search');
    expect(assistant.requires?.features).to.not.include('agent_code');
  });

  it('the feature is known and only added when asked for', () => {
    expect(KNOWN_WORKER_FEATURES).to.include('agent_code');
    const loop = { agentKey: 'a', input: undefined } as unknown as HarnessRun;
    expect(admissionRequirements(loop, [], 'loop', null, null, true).features).to.include('agent_code');
    expect(admissionRequirements(loop).features).to.not.include('agent_code');
  });
});

describe('P5.11 RC-N2: the pin', () => {
  it('agent-code only when the run requires agent_code and the pinned capabilities prove a sandbox', () => {
    expect(pinRunSpec(subject(['agent_code']), PROVED).spec.grants).to.deep.equal({
      ...pinRunSpec(subject(['agent_code']), PROVED).spec.grants,
      sandboxProfile: 'agent-code',
      egress: 'none',
    });
    expect(pinRunSpec(subject(['agent_code']), snapshot('container_gvisor')).spec.grants.sandboxProfile).to.equal(
      'agent-code',
    );
    expect(pinRunSpec(subject(['agent_code']), UNPROVED).spec.grants.sandboxProfile).to.equal('none');
    expect(pinRunSpec(subject([]), PROVED).spec.grants.sandboxProfile).to.equal('none');
    // Admitted before requirements were recorded: nothing says it may run code.
    expect(pinRunSpec(subject(null), PROVED).spec.grants.sandboxProfile).to.equal('none');
    expect(runSpecSchema.safeParse(pinRunSpec(subject(['agent_code']), PROVED).spec).success).to.equal(true);
  });

  it('an engine run never gets the loop sandbox', () => {
    expect(sandboxProfileFor({ requires: { foldVersion: 8, features: ['agent_code'] } }, 'engine', PROVED)).to.equal(
      'none',
    );
  });

  it('the pin is deterministic (stable digest)', () => {
    const a = pinRunSpec(subject(['agent_code']), PROVED);
    const b = pinRunSpec(subject(['agent_code']), PROVED);
    expect(a.specDigest).to.equal(b.specDigest);
  });
});

describe('P5.11 RC-N3: a loop child keeps its parent code sandbox and requires agent_code', () => {
  it('narrowed from an agent-code parent', () => {
    const pinned = pinRunSpec(subject(['agent_code']), PROVED);
    const parent = { ...pinned, spec: { ...pinned.spec, budgets: { ...pinned.spec.budgets, maxChildren: 4 } } };
    const decision = childGrants(parent.spec, {}, 1);
    if (decision.grants === null) expect.fail('expected a grant');
    const child = childRunSpec(parent, decision.grants);
    expect(child.spec.grants.sandboxProfile).to.equal('agent-code');
    expect(childFeatures(child)).to.include('agent_code');
    const plain = pinRunSpec(subject([]), PROVED);
    const plainParent = { ...plain, spec: { ...plain.spec, budgets: { ...plain.spec.budgets, maxChildren: 4 } } };
    const plainChild = childRunSpec(plainParent, childGrants(plainParent.spec, {}, 1).grants!);
    expect(childFeatures(plainChild)).to.not.include('agent_code');
  });
});

describe('P5.11 RC-N4: the sweeper reaps an agent_code run', () => {
  const run = (features: string[], attemptId: string | null = 'att-2'): HarnessRun =>
    ({ orgId: 'org-1', runId: 'run-1', attemptId, requires: { foldVersion: 8, features } }) as unknown as HarnessRun;

  it('by run when it ended, by attempt when its lease lapsed', () => {
    expect(reapLabels(run(['agent_code']), 'deadline_exceeded')).to.deep.equal({ orgId: 'org-1', runId: 'run-1' });
    expect(reapLabels(run(['agent_code']), 'lease_expired')).to.deep.equal({
      orgId: 'org-1',
      runId: 'run-1',
      attemptId: 'att-2',
    });
    expect(reapLabels(run(['chat']), 'lease_expired')).to.equal(null);
  });
});

describe('P5.11 RC-N5: the sandbox isolation probe', () => {
  const health = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
  const probe = (fetchImpl: sinon.SinonStub, endpoint: string | null = 'http://sandbox-broker:8095/') =>
    new HttpSandboxIsolationProbe(
      async () => endpoint,
      async () => 'worker-credential',
      fetchImpl as unknown as typeof fetch,
    );

  it('maps the broker modes, strongest first, with the worker credential', async () => {
    const docker = sinon.stub().resolves(health({ ok: true, modes: ['docker'], active: 0, detail: {} }));
    expect(await probe(docker).probe()).to.deep.equal({ 'sandbox.isolation': 'container' });
    const [url, init] = docker.firstCall.args as [string, RequestInit];
    expect(url).to.equal('http://sandbox-broker:8095/v1/health');
    expect((init.headers as Record<string, string>)['Authorization']).to.equal('Bearer worker-credential');
    const both = sinon.stub().resolves(health({ ok: true, modes: ['docker', 'gvisor'], active: 0, detail: {} }));
    expect(await probe(both).probe()).to.deep.equal({ 'sandbox.isolation': 'container_gvisor' });
    const none = sinon.stub().resolves(health({ ok: true, modes: [], active: 0, detail: {} }));
    expect(await probe(none).probe()).to.deep.equal({ 'sandbox.isolation': 'none' });
    const unhealthy = sinon.stub().resolves(health({ ok: false, modes: ['docker'], active: 0, detail: {} }));
    expect(await probe(unhealthy).probe()).to.deep.equal({ 'sandbox.isolation': 'none' });
  });

  it('no broker proves nothing; one that cannot be asked throws (the service keeps the least value)', async () => {
    const unused = sinon.stub();
    expect(await probe(unused, null).probe()).to.deep.equal({});
    expect(unused.called).to.equal(false);
    const down = sinon.stub().resolves(health({}, 503));
    const failed = await probe(down)
      .probe()
      .catch((error: unknown) => error);
    expect(failed).to.be.instanceOf(Error);
    const malformed = sinon.stub().resolves(health({ ok: true }));
    expect(await probe(malformed).probe().catch((error: unknown) => error)).to.be.instanceOf(Error);
  });

  it('is bound only where run_code uses the broker', () => {
    const container = { get: () => ({ workerCredential: async () => 'c' }) } as never;
    const endpoint = { sandboxBrokerEndpoint: async () => 'http://broker' };
    expect(sandboxIsolationProbes(container, endpoint, { SANDBOX_MODE: 'broker' })).to.have.length(1);
    expect(sandboxIsolationProbes(container, endpoint, { SANDBOX_MODE: 'docker' })).to.have.length(0);
    expect(sandboxIsolationProbes(container, {}, { SANDBOX_MODE: 'broker' })).to.have.length(0);
  });
});
