import { expect } from 'chai';
import { workerCapabilitiesSchema } from '../../../src/modules/agent-harness/contracts/generated/core/worker-capabilities';
import {
  RUN_SPEC_FOLD_VERSION,
  RUN_SPEC_SCHEMA_VERSION,
} from '../../../src/modules/agent-harness/domain/run-spec';
import {
  admissionRequirements,
  attemptAccepts,
  claimingWorker,
  KNOWN_WORKER_FEATURES,
  missingFor,
  raisedByClaim,
  requirementsOf,
  servedSchemaVersion,
  UNADVERTISED_WORKER,
  unattemptedRelease,
  withFeature,
} from '../../../src/modules/agent-harness/domain/worker-compat';
import { steeringFeatures, steeringRefusal } from '../../../src/modules/agent-harness/domain/steering';
import { claimRunSchema } from '../../../src/modules/agent-harness/transport/schemas';
import { CURRENT_WORKER_CAPABILITIES, currentWorker, newerWorker } from './support/worker-capabilities';

const v2 = (requires?: ReturnType<typeof requirementsOf>) => ({
  harnessMajor: 2 as const,
  ...(requires !== undefined ? { requires } : {}),
});

describe('schema-gated claims: the rule (P1b RU-2)', () => {
  it('RU-2a: the advertisement is the contract; unknown feature names are ignored, not refused', () => {
    expect(workerCapabilitiesSchema.parse(CURRENT_WORKER_CAPABILITIES)).to.deep.equal(
      CURRENT_WORKER_CAPABILITIES,
    );
    const newer = claimingWorker({
      ...CURRENT_WORKER_CAPABILITIES,
      features: ['steering', 'chat', 'tasks.v2'],
    });
    expect(newer.features).to.deep.equal(['chat', 'steering']);
  });

  it('RU-2a: a newer worker\'s advertisement with members this Node does not know still claims', () => {
    const body = claimRunSchema.shape.body.parse({
      orgId: 'org-1',
      workerId: 'w-1',
      leaseDurationMs: 1000,
      workerCapabilities: { ...CURRENT_WORKER_CAPABILITIES, maxLeaseMs: 1 },
    });
    expect(body.workerCapabilities).to.deep.equal(CURRENT_WORKER_CAPABILITIES);
  });

  it('RU-2c: admission needs this build\'s fold, and chat or caller only when the run has them', () => {
    const input = {
      query: 'q',
      recordIds: [],
      filters: {},
      attachments: [],
      model: { chatMode: 'quick' },
    };
    expect(admissionRequirements({ agentKey: 'a1', input })).to.deep.equal({
      foldVersion: RUN_SPEC_FOLD_VERSION,
      features: [],
    });
    expect(
      admissionRequirements({ input: { ...input, caller: { email: 'x@y.z' } } }),
    ).to.deep.equal({ foldVersion: RUN_SPEC_FOLD_VERSION, features: ['caller', 'chat'] });
  });

  it('CC-4 (E1): an engine run needs executor_engine; a loop run does not, and it is no session feature', () => {
    const input = {
      query: 'q',
      recordIds: [],
      filters: {},
      attachments: [],
      model: { chatMode: 'quick' },
    };
    expect(admissionRequirements({ agentKey: 'a1', input }, [], 'engine').features).to.deep.equal([
      'executor_engine',
    ]);
    expect(admissionRequirements({ agentKey: 'a1', input }, [], 'loop').features).to.deep.equal([]);
    const needs = admissionRequirements({ agentKey: 'a1', input }, [], 'engine');
    expect(missingFor(v2(needs), currentWorker({ features: [] }))).to.deep.equal([
      'feature:executor_engine',
    ]);
    expect(missingFor(v2(needs), currentWorker())).to.deep.equal([]);
  });

  it('AS1-16: a run pinning the system assistant definition needs assistant_agent; no other run does, and it is no session feature', () => {
    const input = {
      query: 'q',
      recordIds: [],
      filters: {},
      attachments: [],
      model: { chatMode: 'agent' },
    };
    const needs = admissionRequirements({ input }, [], 'loop', null, null, false, false, false, true);
    expect(needs.features).to.deep.equal(['assistant_agent', 'chat']);
    expect(admissionRequirements({ input }).features).to.deep.equal(['chat']);
    expect(missingFor(v2(needs), currentWorker({ features: ['chat'] }))).to.deep.equal([
      'feature:assistant_agent',
    ]);
    expect(missingFor(v2(needs), currentWorker())).to.deep.equal([]);
  });

  it('PIN-28 (CPD-6): a run admitted with its pin read by a worker needs resolved_config; it is no session feature', () => {
    const input = { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'quick' } };
    const needs = admissionRequirements({ agentKey: 'a1', input }, [], 'loop', null, null, false, false, true);
    expect(needs.features).to.deep.equal(['resolved_config']);
    expect(admissionRequirements({ agentKey: 'a1', input }).features).to.deep.equal([]);
    const older = currentWorker({ features: KNOWN_WORKER_FEATURES.filter((f) => f !== 'resolved_config') });
    expect(missingFor(v2(needs), older)).to.deep.equal(['feature:resolved_config']);
    expect(missingFor(v2(needs), currentWorker())).to.deep.equal([]);
  });

  it('RU-2c: a run admitted before T4 needs every known run shape at this fold, never a deployment add-on (sign-in, model fallback) or a worker fact (region_aware, trace_content)', () => {
    expect(requirementsOf({})).to.deep.equal({
      foldVersion: RUN_SPEC_FOLD_VERSION,
      features: KNOWN_WORKER_FEATURES.filter(
        (feature) =>
          !['vendor_signin', 'model_fallback', 'mcp_first_use', 'mcp_carried_pins', 'region_aware', 'trace_content'].includes(feature),
      ),
    });
    // SIGNGAP: a deployment fact, not a run shape; requiring it would strand the run where the add-on is off.
    expect(missingFor(v2(), currentWorker({ features: KNOWN_WORKER_FEATURES.filter((f) => f !== 'vendor_signin') }))).to.deep.equal([]);
    expect(missingFor(v2(), currentWorker())).to.deep.equal([]);
    expect(missingFor(v2(), currentWorker({ features: ['chat'] }))).to.deep.equal([
      'feature:agent_code',
      'feature:approval_edits',
      'feature:assistant_agent',
      'feature:caller',
      'feature:child_asks',
      'feature:child_models',
      'feature:child_runs',
      'feature:code_task_tool',
      'feature:command_input',
      'feature:compact_request',
      'feature:deep_mode',
      'feature:deliverable_pin',
      'feature:descriptor_pins',
      'feature:engine_inputs',
      'feature:environment_pin',
      'feature:executor_engine',
      'feature:governed_memory',
      'feature:inherited_grants',
      'feature:inherited_labels',
      'feature:keyword_search_budget',
      'feature:knowledge_selection',
      'feature:output_locale',
      'feature:release_pin',
      'feature:resolved_config',
      'feature:run_gates',
      'feature:run_surface',
      'feature:sandbox_command',
      'feature:session_roles',
      'feature:steering',
      'feature:steering_by',
      'feature:steering_references',
      'feature:trigger_context',
      'feature:turn_labels',
      'feature:workspace_forks',
    ]);
  });

  it('RU-2: an older worker never gets a run a newer build claimed; a newer worker gets an older run', () => {
    const touched = raisedByClaim(requirementsOf(v2({ foldVersion: 1, features: [] })), newerWorker());
    expect(touched.foldVersion).to.equal(RUN_SPEC_FOLD_VERSION + 1);
    expect(missingFor(v2(touched), currentWorker())).to.deep.equal(['foldVersion']);
    expect(missingFor(v2({ foldVersion: 1, features: [] }), newerWorker())).to.deep.equal([]);
  });

  it('RU-2d: a worker that advertises nothing can run no v2 run, and still retires v1 runs', () => {
    expect(missingFor(v2({ foldVersion: 1, features: [] }), UNADVERTISED_WORKER)).to.deep.equal([
      'workerSchemaVersion',
      'foldVersion',
    ]);
    expect(missingFor({ harnessMajor: 1 }, UNADVERTISED_WORKER)).to.deep.equal([]);
  });

  it('RU-2g: a Node never serves a run pinned with a spec schema newer than it reads', () => {
    const pinned = { foldVersion: 1, features: [], schemaVersion: RUN_SPEC_SCHEMA_VERSION + 1 };
    expect(missingFor(v2(pinned), currentWorker())).to.deep.equal(['schemaVersion', 'workerSchemaVersion']);
    expect(
      missingFor(v2(pinned), currentWorker({ supportedSchemaVersions: [RUN_SPEC_SCHEMA_VERSION, RUN_SPEC_SCHEMA_VERSION + 1] })),
    ).to.deep.equal(['schemaVersion']);
    expect(
      missingFor(v2({ ...pinned, schemaVersion: RUN_SPEC_SCHEMA_VERSION }), currentWorker({ supportedSchemaVersions: [9] })),
    ).to.deep.equal(['workerSchemaVersion']);
  });

  it('RU-2 (F2): a worker is matched on the spec version it would be handed, so a spec bump needs no new gate', () => {
    const older = RUN_SPEC_SCHEMA_VERSION - 1;
    const pinnedOlder = { foldVersion: 1, features: [], schemaVersion: older };
    expect(servedSchemaVersion(pinnedOlder)).to.equal(older);
    expect(servedSchemaVersion({ foldVersion: 1, features: [] })).to.equal(RUN_SPEC_SCHEMA_VERSION);
    expect(missingFor(v2(pinnedOlder), currentWorker({ supportedSchemaVersions: [older] }))).to.deep.equal([]);
    expect(
      missingFor(v2(pinnedOlder), currentWorker({ supportedSchemaVersions: [RUN_SPEC_SCHEMA_VERSION] })),
    ).to.deep.equal(['workerSchemaVersion']);
    // This build's worker reads both, so a run pinned by the previous release keeps going.
    expect(missingFor(v2(pinnedOlder), currentWorker())).to.deep.equal([]);
    // Not pinned yet: this Node pins at its own version, which the worker must read.
    expect(
      missingFor(v2({ foldVersion: 1, features: [] }), currentWorker({ supportedSchemaVersions: [older] })),
    ).to.deep.equal(['workerSchemaVersion']);
  });

  it('RU-2f: steering is refused while the attempt\'s worker cannot fold it', () => {
    const text = { query: 'more', attachments: [], recordIds: [] };
    const running = { harnessMajor: 2 as const, status: 'running' as const };
    expect(steeringRefusal({ ...running, attemptFeatures: ['chat'] }, text)).to.equal('worker');
    expect(steeringRefusal({ ...running, attemptFeatures: ['steering', 'steering_by'] }, text)).to.equal(null);
    expect(steeringRefusal({ ...running, status: 'queued', attemptFeatures: ['chat'] }, text)).to.equal(null);
    expect(attemptAccepts({}, 'steering')).to.equal(true);
    expect(withFeature({ foldVersion: 5, features: ['chat'] }, 'steering').features).to.deep.equal([
      'chat',
      'steering',
    ]);
  });

  it('RU-2g (X1): a message naming attachments or records also needs steering_references', () => {
    const text = { attachments: [], recordIds: [] };
    const refs = { attachments: [{ recordId: 'r', virtualRecordId: 'v' }], recordIds: [] };
    const running = { harnessMajor: 2 as const, status: 'running' as const };
    expect(steeringFeatures({ attachments: [], recordIds: ['r'] })).to.deep.equal([
      'steering',
      'steering_by',
      'steering_references',
    ]);
    expect(steeringRefusal({ ...running, attemptFeatures: ['steering', 'steering_by'] }, refs)).to.equal('worker');
    expect(steeringRefusal({ ...running, attemptFeatures: ['steering', 'steering_by'] }, text)).to.equal(null);
    expect(
      steeringRefusal({ ...running, attemptFeatures: ['steering', 'steering_by', 'steering_references'] }, refs),
    ).to.equal(null);
    expect(withFeature({ foldVersion: 7, features: ['chat'] }, ...steeringFeatures(refs)).features).to.deep.equal([
      'chat',
      'steering',
      'steering_by',
      'steering_references',
    ]);
  });

  it('QB-2 (SS1): every queued message names its sender, so it needs steering_by', () => {
    const text = { attachments: [], recordIds: [] };
    const running = { harnessMajor: 2 as const, status: 'running' as const };
    expect(steeringFeatures(text)).to.deep.equal(['steering', 'steering_by']);
    // A worker from before SS1 folds queued input but would refuse `by`.
    expect(steeringRefusal({ ...running, attemptFeatures: ['chat', 'steering'] }, text)).to.equal('worker');
    expect(steeringRefusal({ ...running, status: 'queued', attemptFeatures: ['steering'] }, text)).to.equal(null);
  });

  it('NK-5 (K-L): a deep run needs deep_mode; no other mode does', () => {
    const input = (chatMode: string) => ({
      query: 'q',
      recordIds: [],
      filters: {},
      attachments: [],
      model: { chatMode },
    });
    expect(admissionRequirements({ agentKey: 'a1', input: input('deep') }).features).to.deep.equal([
      'deep_mode',
    ]);
    expect(admissionRequirements({ agentKey: 'a1', input: input('DEEP') }).features).to.deep.equal([
      'deep_mode',
    ]);
    expect(admissionRequirements({ agentKey: 'a1', input: input('auto') }).features).to.deep.equal([]);
    expect(admissionRequirements({ agentKey: 'a1', input: input('react') }).features).to.deep.equal([]);
    // The built-in assistant never runs deep mode.
    expect(admissionRequirements({ input: input('deep') }).features).to.deep.equal(['chat']);
    const deep = v2(admissionRequirements({ agentKey: 'a1', input: input('deep') }));
    expect(missingFor(deep, currentWorker({ features: ['caller', 'chat', 'child_runs'] }))).to.deep.equal([
      'feature:deep_mode',
    ]);
    expect(missingFor(deep, currentWorker())).to.deep.equal([]);
  });

  it('QB-3 (SS1): a run admitted into a session inherits what its stream already holds', () => {
    const input = { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } };
    expect(admissionRequirements({ agentKey: 'a1', input }, ['steering', 'steering_by']).features).to.deep.equal([
      'steering',
      'steering_by',
    ]);
    // A name this Node does not know is not a requirement it could match.
    expect(admissionRequirements({ agentKey: 'a1', input }, ['from_the_future']).features).to.deep.equal([]);
  });

  it('RU-1a: only a release before the runtime was built takes back its attempt', () => {
    expect(unattemptedRelease('drain')).to.equal(true);
    for (const reason of ['drain_timeout', 'fold_incompatible', 'journal_unavailable', 'assembly_failed']) {
      expect(unattemptedRelease(reason), reason).to.equal(false);
    }
  });
});
