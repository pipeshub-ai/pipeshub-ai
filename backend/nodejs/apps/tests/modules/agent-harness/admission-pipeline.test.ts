/**
 * CPD-5 S1 (R2 CP11, R3 WP-10): admission as ordered steps, one refusal
 * classifier, and the run document and its executor decided once in domain
 * for both stores. The full submit and drain behaviour is held by the
 * existing admission suites, unchanged (CPD5-3, CPD5-4).
 */
import { expect } from 'chai';
import {
  AdmissionPipeline,
  AdmissionStep,
  concurrently,
} from '../../../src/modules/agent-harness/application/admission/pipeline';
import {
  AdmissionRefusal,
  asRefusal,
  isKnownRefusal,
} from '../../../src/modules/agent-harness/application/admission/refusals';
import {
  AdmitRunCommand,
  executorKindOf,
  rootRunDocument,
  rootRunRequirements,
} from '../../../src/modules/agent-harness/domain/admission';
import {
  HarnessConflictError,
  HarnessForbiddenError,
  HarnessQuotaExceededError,
} from '../../../src/modules/agent-harness/domain/errors';
import { RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';

interface Trace {
  seen: string[];
}

const step = (
  name: string,
  behave: (trace: Trace) => Promise<void> | void = () => undefined,
): AdmissionStep<Trace> => ({
  name,
  run: async (trace) => {
    trace.seen.push(name);
    await behave(trace);
  },
});

const refuse = (reason: string) => (): never => {
  throw new AdmissionRefusal(reason, new HarnessForbiddenError());
};

const later = (ms: number): Promise<void> =>
  new Promise((resolve) => setImmediate(() => setTimeout(resolve, ms)));

describe('CPD-5 admission pipeline (CPD5-1, CPD5-2, CPD5-5)', () => {
  it('CPD5-1: steps run in order on one context; the first refusal stops the rest', async () => {
    const trace: Trace = { seen: [] };
    const pipeline = new AdmissionPipeline([
      step('a'),
      step('b', (t) => {
        expect(t.seen).to.deep.equal(['a', 'b']);
      }),
      step('c', refuse('narrowing')),
      step('d'),
    ]);
    const error = await pipeline.run(trace).catch((e: unknown) => e);
    expect(error).to.be.instanceOf(AdmissionRefusal);
    expect((error as AdmissionRefusal).reason).to.equal('narrowing');
    expect(trace.seen).to.deep.equal(['a', 'b', 'c']);
  });

  it('CPD5-2: concurrent steps both run; the refusal first in list order wins whatever settles first', async () => {
    const trace: Trace = { seen: [] };
    const group = concurrently('participants', [
      step('slow', async () => {
        await later(5);
        refuse('owner_only')();
      }),
      step('fast', refuse('memory')),
    ]);
    const error = await new AdmissionPipeline([group, step('after')])
      .run(trace)
      .catch((e: unknown) => e);
    expect((error as AdmissionRefusal).reason).to.equal('owner_only');
    expect(trace.seen).to.have.members(['slow', 'fast']);
    expect(trace.seen).not.to.include('after');
  });

  it('CPD5-5: one classifier names every refusal; an unknown error is a failure, not a refusal', () => {
    expect(asRefusal(new HarnessQuotaExceededError('org', 'active_runs', 5)).reason).to.equal('quota');
    expect(asRefusal(new HarnessConflictError('RUN_ACTIVE', 'busy')).reason).to.equal('run_active');
    const passed = new AdmissionRefusal('narrowing', new HarnessForbiddenError());
    expect(asRefusal(passed)).to.equal(passed);
    const unknown = asRefusal(new TypeError('boom'));
    expect(unknown.reason).to.equal('unavailable');
    expect(isKnownRefusal(unknown.error)).to.equal(false);
    expect(isKnownRefusal(new HarnessForbiddenError())).to.equal(true);
  });
});

describe('CPD-5 R3 WP-10: the root run and its executor in domain (CPD5-6, CPD5-7)', () => {
  const command = (extra: Partial<AdmitRunCommand> = {}): AdmitRunCommand => ({
    orgId: 'o1',
    actorId: 'u1',
    agentKey: 'a1',
    sessionId: null,
    idempotencyKey: 'k',
    payloadHash: 'sha256:00',
    runId: 'r1',
    dispatchCommandId: 'dispatch:1',
    absoluteDeadlineAt: 2_000,
    input: {
      query: 'q',
      model: { chatMode: 'quick' },
      filters: {},
      attachments: [],
      recordIds: [],
      previousConversations: [],
    } as unknown as AdmitRunCommand['input'],
    harnessMajor: 2,
    via: 'session',
    ...extra,
  });

  it('CPD5-6: engine exactly when admission resolved an engine profile; requirements follow it', () => {
    const profile = RELEASE_ENGINE_PROFILES[0]!;
    expect(executorKindOf(command())).to.equal('loop');
    expect(executorKindOf(command({ engineProfile: profile }))).to.equal('engine');
    const placement = {
      sessionId: 's1',
      userMessageKey: 'm1',
      idempotencyKey: 'd',
      absoluteDeadlineAt: 2_000,
      input: command().input,
      now: 1_000,
    };
    const engine = command({ engineProfile: profile });
    const loopNeeds = rootRunRequirements(rootRunDocument(command(), placement), command(), [], null, null);
    const engineNeeds = rootRunRequirements(rootRunDocument(engine, placement), engine, [], null, null);
    expect(engineNeeds?.features).to.include('executor_engine');
    expect(loopNeeds?.features).not.to.include('executor_engine');
    expect(
      rootRunRequirements(rootRunDocument(command({ harnessMajor: 1 }), placement), command({ harnessMajor: 1 }), [], null, null),
    ).to.equal(undefined);
  });

  it('CPD5-7: a root run is queued, unclaimed and placed where the store says', () => {
    const run = rootRunDocument(command({ profile: 'inline' }), {
      sessionId: 's1',
      userMessageKey: 'm1',
      idempotencyKey: 'digest',
      absoluteDeadlineAt: 2_000,
      input: command().input,
      now: 1_000,
    });
    expect(run).to.deep.include({
      runId: 'r1',
      sessionId: 's1',
      agentKey: 'a1',
      idempotencyKey: 'digest',
      profile: 'inline',
      status: 'queued',
      fence: 0,
      attempts: 0,
      notBefore: 1_000,
      createdAt: 1_000,
      absoluteDeadlineAt: 2_000,
    });
    expect(rootRunDocument(command({ agentKey: undefined }), {
      sessionId: 's1',
      userMessageKey: 'm1',
      idempotencyKey: 'd',
      absoluteDeadlineAt: 2_000,
      input: command().input,
      now: 1,
    })).not.to.have.property('agentKey');
  });
});
