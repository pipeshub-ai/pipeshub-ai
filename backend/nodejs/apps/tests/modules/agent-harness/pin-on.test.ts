/**
 * CPD-5 S6: `CONTROL_PLANE_PIN=on`. The pin is the run's: written in the
 * admission's transaction behind the head check, the spec's budgets derived
 * from it, `x.pipeshub.resolved` carried only for a worker that reads it. The
 * real admission service and in-memory store; the production pinner over
 * CPD-3's resolver and the control plane's in-memory heads.
 */
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { expect } from 'chai';
import { releaseLayers } from '../../../src/modules/agent-harness/domain/pin-shadow';
import fc from 'fast-check';
import { randomUUID } from 'crypto';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { PolicyPinning, type PinWorkerFeatures } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import { NextRunService } from '../../../src/modules/agent-harness/application/next-run-service';
import { PinStale } from '../../../src/modules/agent-harness/domain/admission';
import { configuredModels } from '../../../src/modules/agent-harness/domain/configured-llms';
import { readmissionBy } from './support/next-runs';
import { RunAdmissionService, SubmitDurableRun } from '../../../src/modules/agent-harness/application/run-admission-service';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { RELEASE_DEFAULTS } from '../../../src/modules/agent-harness/contracts/generated/core/policy-data';
import type { PolicyLayer } from '../../../src/modules/agent-harness/contracts/generated/core/policy/policy-layer';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { pinnedBudgets, pinnedProfileFields } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import type { HarnessRolloutSettings } from '../../../src/modules/agent-harness/domain/rollout';
import { legacyBudgets, pinRunSpec, profileExecutor } from '../../../src/modules/agent-harness/domain/run-spec';
import {
  PolicyRunConfigPinner,
  prefixSources,
  ResolvedSnapshotCache,
  runSources,
  type ControlPlanePin,
  type RunConfigPinner,
} from '../../../src/modules/control-plane';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { pinnerOf } from './support/a-pin';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { committingTx, InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';
import { FakeEngineCeilings, FakeModelAccessPolicies } from './support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const ORG = 'org-on';
const LABS: HarnessRolloutSettings = { infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

describe('CPD-5 CONTROL_PLANE_PIN=on (PIN-22, CPD5-50, CPD5-51, CPD5-54)', () => {
  let store: InMemoryExecutionStore;
  let heads: InMemoryHeadRevisions;
  let stored: Map<string, PolicyLayer>;
  let cache: ResolvedSnapshotCache;
  let pins: number;
  let afterPin: () => unknown;
  let aiModels: unknown;

  const pinner = (): RunConfigPinner => {
    const inner = new PolicyRunConfigPinner({
      snapshots: cache,
      engine: new PolicyResolver(),
      effectiveSet: { resolve: () => ({ items: [], digest: canonicalDigest([]) }) },
      releases: releaseLayers(),
      prefix: prefixSources({ modelAliases: ['pipeshub:org-default'] }),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
    });
    return {
      route: (subject) => inner.route(subject),
      pin: async (subject, basis) => {
        pins += 1;
        const pin = await inner.pin(subject, basis);
        await afterPin();
        return pin;
      },
    };
  };

  const service = (
    mode: ControlPlanePin,
    pinWith: RunConfigPinner = pinner(),
    definition = (agentKey: string) => agentDefinition({ agentKey }),
    // No live worker reads the pin: the spec keeps the fields `off` pins (the F2 rule; pin-complete.test.ts has the other half).
    // A live worker (`chat`) that does not read pins (ADR-CAP-01 D2: only that proves the feature absent).
    workerFeatures: PinWorkerFeatures = { live: async (feature) => feature === 'chat', metrics: { withheld: () => undefined } },
  ) => {
    const step = new PolicyPinning({
      mode,
      pinner: pinWith,
      metrics: { mismatch: () => undefined },
      logger: { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined },
      refresh: (orgId) => cache.refresh(orgId),
      workerFeatures,
    });
    return new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      agentAccess: grantingAccess(definition),
      roles: store,
      collaboratorTurns: { enabled: async () => true },
      engineProfiles: new FakeEngineCeilings(),
      modelAccess: testModelAccess({
        policies: new FakeModelAccessPolicies(),
      }),
      executors: profileExecutor,
      policySteps: [step],
      repin: (context) => step.repin(context),
    });
  };

  const admit = async (admission: RunAdmissionService, request: Partial<SubmitDurableRun> = {}) => {
    const result = await admission.submit({
      orgId: ORG,
      actorId: 'u3',
      agentKey: 'a7',
      authorization: 'Bearer u3',
      idempotencyKey: randomUUID(),
      deadlineMs: 600_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
      ...request,
    });
    if (result.route !== 'durable') return expect.fail('expected a durable admission');
    return result.admission;
  };

  beforeEach(() => {
    store = new InMemoryExecutionStore();
    heads = new InMemoryHeadRevisions();
    store.controlPlaneHeads = (orgId) => heads.current(orgId);
    stored = new Map();
    cache = new ResolvedSnapshotCache({
      load: policySnapshotLoader({
        heads,
        layers: async () => stored,
        rollout: { read: async () => LABS },
        models: { read: async () => configuredModels(aiModels) },
      }),
      heads,
    });
    pins = 0;
    afterPin = () => undefined;
    aiModels = null;
  });

  it('CPD5-50: the run holds the pin; its spec is the spec off pins for the same inputs', async () => {
    const off = await admit(service('off'));
    const on = await admit(service('on'));
    const [a, b] = await Promise.all([off, on].map((x) => store.readClaimSubject(ORG, x.run.runId)));
    expect(a?.resolved).to.equal(null);
    expect(b?.resolved?.headRevision).to.equal(0);
    expect(b?.resolved?.routing).to.deep.equal({ path: 'durable', reason: null });
    const specOf = (subject: typeof a) => pinRunSpec({ ...subject!, run: { ...subject!.run, runId: 'r', createdAt: 0, absoluteDeadlineAt: 600_000 } }, DURABLE).spec;
    expect(specOf(b)).to.deep.equal(specOf(a));
  });

  it('PIN-22: a control-plane write between the pin and the commit: re-pinned once, the run carries the new head', async () => {
    afterPin = () => {
      afterPin = () => undefined;
      heads.commit(ORG);
    };
    const admitted = await admit(service('on'));
    expect(pins).to.equal(2);
    expect((await store.readClaimSubject(ORG, admitted.run.runId))?.resolved?.headRevision).to.equal(1);
  });

  describe('CPD10-40: an AI-model save commits between the pin and the commit', () => {
    const llm = (provider: string, model: string) => ({ llm: [{ modelKey: 'mk-1', provider, isDefault: true, configuration: { model } }] });
    /** Saved by an admin of another org: this org's head stays, the deployment's revision moves. */
    const saved = async (models: unknown) => {
      aiModels = models;
      await heads.move(committingTx('org-elsewhere'));
    };
    const unnamed = (agentKey: string) => agentDefinition({ agentKey, models: [] });

    it('caught on the deployment revision, re-pinned once, and the run’s llm is the new one', async () => {
      aiModels = llm('openAI', 'gpt-x');
      afterPin = async () => {
        afterPin = () => undefined;
        await saved(llm('anthropic', 'claude-z'));
      };
      const admitted = await admit(service('on', pinner(), unnamed));
      expect(pins).to.equal(2);
      const pin = (await store.readClaimSubject(ORG, admitted.run.runId))?.resolved;
      expect(pin?.llm).to.deep.include({ modelKey: 'mk-1', provider: 'anthropic', model: 'claude-z' });
      expect(pin?.headRevision, 'the org wrote nothing').to.equal(0);
      expect(heads.current(ORG)).to.deep.equal({ org: 0, deployment: 1 });
    });

    it('with no save in between the first pin is the run’s', async () => {
      aiModels = llm('openAI', 'gpt-x');
      const admitted = await admit(service('on', pinner(), unnamed));
      expect(pins).to.equal(1);
      expect((await store.readClaimSubject(ORG, admitted.run.runId))?.resolved?.llm).to.deep.include({ provider: 'openai', model: 'gpt-x' });
    });

    it('saves that keep committing fail the admission as unavailable, nothing written', async () => {
      aiModels = llm('openAI', 'gpt-x');
      afterPin = () => saved(llm('anthropic', 'claude-z'));
      const refused = await admit(service('on', pinner(), unnamed)).catch((e: unknown) => e);
      expect(refused).to.include({ name: 'HarnessRunConfigUnavailableError', code: 'RUN_CONFIG_UNAVAILABLE' });
      expect(pins).to.equal(2);
    });

    it('CPD10-41: a drained next run’s pin is held to the same revisions: it keeps its place, and is admitted on the next drain', async () => {
      aiModels = llm('openAI', 'gpt-x');
      const admission = service('on', pinner(), unnamed);
      const first = await admit(admission, { actorId: 'alice', authorization: 'Bearer alice' });
      await store.failRun(ORG, first.run.runId, 'ended', []);
      store.shareSession(ORG, first.sessionId, 'bob', 'write');
      const bobs = await admit(admission, { actorId: 'bob', authorization: 'Bearer bob', sessionId: first.sessionId });
      await admission.submit({
        orgId: ORG,
        actorId: 'alice',
        agentKey: 'a7',
        authorization: 'Bearer alice',
        sessionId: first.sessionId,
        idempotencyKey: randomUUID(),
        deadlineMs: 600_000,
        input: { query: 'next', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
      });
      await store.failRun(ORG, bobs.run.runId, 'ended', []);
      const drains = new NextRunService({ store, readmission: readmissionBy(() => admission), collaboratorTurns: { enabled: async () => true } });
      pins = 0;
      afterPin = async () => {
        afterPin = () => undefined;
        await saved(llm('anthropic', 'claude-z'));
      };
      expect(await drains.drain(ORG, first.sessionId).catch((e: unknown) => e)).to.be.instanceOf(PinStale);
      expect((await store.nextPending(ORG, first.sessionId))?.actorId).to.equal('alice');
      expect(await drains.drain(ORG, first.sessionId)).to.equal('admitted');
      expect(pins).to.equal(2);
      const drained = [...(store as unknown as { runs: Map<string, { runId: string; actorId: string; createdAt: number }> }).runs.values()]
        .filter((run) => run.actorId === 'alice')
        .sort((a, b) => b.createdAt - a.createdAt)[0]!;
      expect((await store.readClaimSubject(ORG, drained.runId))?.resolved?.llm).to.deep.include({ provider: 'anthropic', model: 'claude-z' });
    });
  });

  it('CPD-6: the re-pin decides again whether a worker reads the pin', async () => {
    afterPin = () => {
      afterPin = () => undefined;
      heads.commit(ORG);
    };
    const step = new PolicyPinning({
      mode: 'on',
      pinner: pinner(),
      metrics: { mismatch: () => undefined },
      logger: { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined },
      refresh: (orgId) => cache.refresh(orgId),
      // A worker that reads the pin shows up between the first pin and the re-pin.
      workerFeatures: { live: async (feature) => feature === 'chat' || pins >= 2, metrics: { withheld: () => undefined } },
    });
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey })),
      executors: profileExecutor,
      policySteps: [step],
      repin: (context) => step.repin(context),
    }).submit({
      orgId: ORG,
      actorId: 'u3',
      agentKey: 'a7',
      authorization: 'Bearer u3',
      idempotencyKey: randomUUID(),
      deadlineMs: 600_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });
    if (outcome.route !== 'durable') return expect.fail('expected a durable admission');
    expect(pins).to.equal(2);
    expect(outcome.admission.run.requires?.features).to.include('resolved_config');
  });

  it('PIN-22: a head that keeps moving fails the admission as unavailable, nothing written', async () => {
    afterPin = () => heads.commit(ORG);
    const refused = await admit(service('on')).catch((e: unknown) => e);
    expect(refused).to.include({ name: 'HarnessRunConfigUnavailableError', code: 'RUN_CONFIG_UNAVAILABLE' });
    expect(pins).to.equal(2);
  });

  it('on: a pin the control plane refuses refuses the run; one it cannot make fails closed', async () => {
    stored.set('agent:a7', { schemaVersion: 1, scope: { kind: 'agent' }, budgets: { activeTimeS: 60 } });
    const refused = await admit(service('on')).catch((e: unknown) => e);
    expect(refused).to.have.property('name', 'HarnessForbiddenError');
    const failing = await admit(service('on', pinnerOf(async () => Promise.reject(new Error('down'))))).catch((e: unknown) => e);
    expect(failing).to.include({ name: 'HarnessRunConfigUnavailableError', code: 'RUN_CONFIG_UNAVAILABLE' });
    // Typed on the wire, so the UI words it (CPD6-37): 503, retryable, never the generic unavailable.
    expect(transportError(failing)).to.include({ code: 'HTTP_AGENT_HARNESS_RUN_CONFIG_UNAVAILABLE', statusCode: 503 });
  });

  it('CPD10-44: with the policy store as the record no run is admitted without a worker reading its pin; nothing is withheld', async () => {
    const withheld: string[] = [];
    const asked: string[] = [];
    const noWorkerReadsThePin = (pinOnly: boolean): PinWorkerFeatures => ({
      pinOnly,
      live: async (feature) => {
        asked.push(feature);
        return feature === 'chat';
      },
      metrics: { withheld: (feature) => void withheld.push(feature) },
    });

    const dual = await admit(service('on', pinner(), undefined, noWorkerReadsThePin(false)));
    expect(dual.run.requires?.features).not.to.include('resolved_config');
    expect(withheld).to.deep.equal(['resolved_config']);

    withheld.length = 0;
    asked.length = 0;
    const on = await admit(service('on', pinner(), undefined, noWorkerReadsThePin(true)));
    expect(on.run.requires?.features).to.include('resolved_config');
    expect(withheld).to.deep.equal([]);
    expect(asked).to.deep.equal([]);
    const subject = (await store.readClaimSubject(ORG, on.run.runId))!;
    const spec = pinRunSpec(subject, DURABLE).spec;
    expect((spec.extensions?.['x.pipeshub'] as { resolved?: { digest: string } }).resolved?.digest).to.equal(subject.resolved?.digest);
  });

  it('CPD10-44: a re-pin under the policy store as the record still requires the pin', async () => {
    afterPin = () => {
      afterPin = () => undefined;
      heads.commit(ORG);
    };
    const admitted = await admit(
      service('on', pinner(), undefined, { pinOnly: true, live: async () => false, metrics: { withheld: () => expect.fail('nothing is withheld') } }),
    );
    expect(pins).to.equal(2);
    expect(admitted.run.requires?.features).to.include('resolved_config');
  });

  it('CPD5-51: x.pipeshub.resolved only for a run that requires resolved_config', async () => {
    const admitted = await admit(service('on'));
    const subject = (await store.readClaimSubject(ORG, admitted.run.runId))!;
    const plain = pinRunSpec(subject, DURABLE).spec;
    expect(plain.extensions?.['x.pipeshub']).not.to.have.property('resolved');
    const reading = pinRunSpec(
      { ...subject, run: { ...subject.run, requires: { foldVersion: 8, features: ['resolved_config' as never] } } },
      DURABLE,
    ).spec;
    expect((reading.extensions?.['x.pipeshub'] as { resolved?: { digest: string } }).resolved?.digest).to.equal(subject.resolved?.digest);
  });

  it('CPD5-54: budgets and engine limits are one dimension: the pinned budgets equal today’s merge', async () => {
    const resolver = new PolicyResolver();
    fc.assert(
      fc.property(
        fc.record({
          maxTokens: fc.option(fc.integer({ min: 1, max: 2_000_000 }), { nil: null }),
          maxUsd: fc.option(fc.integer({ min: 0, max: 9 }), { nil: null }),
          maxWallS: fc.option(fc.integer({ min: 1, max: 1_800 }), { nil: undefined }),
        }),
        fc.integer({ min: 1, max: 86_400 }),
        fc.option(fc.integer({ min: 1, max: 5 }), { nil: undefined }),
        (limits, activeTimeS, maxCostUsd) => {
          const resolved = resolver.resolve([
            { kind: 'release', values: RELEASE_DEFAULTS },
            { kind: 'agent', values: { engineProfile: { limits: JSON.parse(JSON.stringify(limits)) as object } } },
            ...(maxCostUsd !== undefined ? [{ kind: 'trigger' as const, values: { budgets: { maxCostUsd } } }] : []),
            { kind: 'request', values: { budgets: { activeTimeS } } },
          ]);
          expect(resolved.refusal).to.equal(null);
          // Today's merge over the profile the same layers resolve to.
          const profile = pinnedProfileFields(resolved.config!) as never;
          expect(pinnedBudgets(resolved.config!, 'engine')).to.deep.equal(
            legacyBudgets(profile, maxCostUsd === undefined ? null : { maxCostUsd }, activeTimeS),
          );
        },
      ),
      { numRuns: 200, seed: 20261011 },
    );
  });
});
