/**
 * A1 AD: an engine run's model access is decided once and pinned. Since CTL-6
 * a coder agent's turn runs on the loop and the access is decided when it
 * hands off to its code_task engine child, which pins it; a loop turn naming an
 * account is refused at admission. In memory: RunAdmissionService → InMemoryExecutionStore →
 * ExecutionService.claim; model accounts in the real vault over an in-memory
 * key-value store; the shipped signed vendor rules.
 */
import { expect } from 'chai';
import { ModelAccountVault } from '../../../src/modules/agent-harness/adapters/configuration/model-account-vault';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import {
  ModelAccessService,
  ModelAccessServiceOptions,
  offeringFromEnv,
} from '../../../src/modules/agent-harness/application/model-access-service';
import {
  RunAdmissionService,
  SubmitDurableRun,
} from '../../../src/modules/agent-harness/application/run-admission-service';
import { childEngineAuthority, ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { codeTaskAuthority } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { EngineProfileNarrowing } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { HarnessEngineProfileRefusedError, HarnessModelAccessRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import {
  gatewayGrantDigests,
  grantDigest,
  grantedRoutes,
} from '../../../src/modules/agent-harness/domain/gateway-token';
import { ModelAccessRefusal } from '../../../src/modules/agent-harness/domain/model-access';
import { pinRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { InMemoryConfiguredModels } from './support/model-access';
import { KEYS, MemoryKv, platformCipher } from './support/model-accounts';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings, FakeModelAccessPolicies } from './support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const ENGINE = { engines: ['opencode'] };
const OWN_KEY = 'users/user-1/openAI';

describe('A1 AD: an engine child pins its model access, decided once', () => {
  let now: number;
  let store: InMemoryExecutionStore;
  let service: ExecutionService;
  let vault: ModelAccountVault;
  let models: InMemoryConfiguredModels;
  let policies: FakeModelAccessPolicies;
  let ceilings: FakeEngineCeilings;
  let options: Partial<ModelAccessServiceOptions>;

  const admit = async (
    definition: Parameters<typeof agentDefinition>[0],
    model: Partial<RunInputSnapshot['model']> = {},
    request: Partial<SubmitDurableRun> = {},
  ) => {
    const access = new ModelAccessService({
      offering: 'oss',
      policies,
      models,
      accounts: vault,
      ...options,
    });
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => now },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey, ...definition })),
      engineProfiles: ceilings,
      modelAccess: access,
      roles: store,
      collaboratorTurns: { enabled: async () => true },
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${String(now)}-${Math.random()}`,
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto', ...model } },
      ...request,
    });
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    const claimed = await service.claim({
      orgId: 'org-1',
      runId: outcome.admission.run.runId,
      workerId: 'w',
      attemptId: `a-${outcome.admission.run.runId}`,
      leaseDurationMs: 1_000,
    });
    if (codeTaskAuthority(claimed.pinned!.spec) === null) {
      return { run: outcome.admission.run, spec: claimed.pinned!.spec };
    }
    // The coder agent's turn hands off: its engine child is where access is decided and pinned (CTL-6).
    const spawned = await new ChildRunService({
      runs: store,
      children: store,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      workspaces: null,
      engineAuthority: childEngineAuthority(ceilings),
      engineAccess: (subject) => access.decide(subject),
    }).spawn(
      { orgId: 'org-1', runId: claimed.run.runId, workerId: 'w', attemptId: `a-${claimed.run.runId}`, fence: claimed.run.fence },
      { invocationId: 't1.0', goal: 'build it', grants: {}, executor: { kind: 'engine' } },
    );
    const child = await store.readClaimSubject('org-1', spawned.childRunId);
    return { run: outcome.admission.run, spec: child!.pinned!.spec };
  };

  const refusal = async (
    definition: Parameters<typeof agentDefinition>[0],
    model: Partial<RunInputSnapshot['model']> = {},
    request: Partial<SubmitDurableRun> = {},
  ): Promise<unknown> => {
    // A refused turn writes nothing; a refused hand-off writes no child (the turn itself ran).
    const runs = (store as unknown as { runs: Map<string, { parent?: unknown }> }).runs;
    const children = () => [...runs.values()].filter((run) => run.parent != null).length;
    const before = children();
    const roots = runs.size - before;
    try {
      await admit(definition, model, request);
    } catch (error) {
      expect(children(), 'no child written').to.equal(before);
      expect(runs.size - children(), 'at most the turn written').to.be.at.most(roots + 1);
      return error;
    }
    return expect.fail('expected a refusal');
  };

  const accessCode = (error: unknown): [string, string | null] => {
    if (!(error instanceof HarnessModelAccessRefusedError)) throw error;
    return [error.code, error.rule];
  };

  beforeEach(async () => {
    now = 1_000;
    options = {};
    store = new InMemoryExecutionStore({ now: () => now });
    service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: pinRunSpec,
    });
    vault = new ModelAccountVault(new MemoryKv(), platformCipher);
    await vault.put('org-1', { kind: 'user', userId: 'user-1' }, 'openAI', {
      kind: 'user_api_key',
      provider: 'openAI',
      label: null,
      material: { provider: 'openAI', apiKey: KEYS.openAI },
      hint: KEYS.openAI,
    }, 'user-1');
    await vault.put('org-1', { kind: 'user', userId: 'user-2' }, 'openAI', {
      kind: 'user_api_key',
      provider: 'openAI',
      label: null,
      material: { provider: 'openAI', apiKey: KEYS.openAIRotated },
      hint: KEYS.openAIRotated,
    }, 'user-2');
    await vault.put('org-1', { kind: 'org' }, 'acc-1', {
      kind: 'org_cloud',
      provider: 'azureOpenAI',
      label: 'EU',
      material: { provider: 'azureOpenAI', apiKey: KEYS.azure, endpoint: 'https://x.openai.azure.com', deploymentName: 'gpt-5' },
      hint: KEYS.azure,
    }, 'admin');
    models = new InMemoryConfiguredModels();
    policies = new FakeModelAccessPolicies();
    ceilings = new FakeEngineCeilings();
  });

  it('AD-1: an engine run on the org default pins the concrete configured model, never org-default', async () => {
    const { spec } = await admit({ models: [], engineProfile: ENGINE });
    expect(spec.model.policy).to.deep.equal([
      { provider: 'openai', model: 'gpt-5', access: { route: 'gateway', accountKind: 'org_api_key', pays: 'org' } },
    ]);
    expect(spec.extensions?.['x.pipeshub']).to.deep.include({ model: { modelKey: 'm-default', modelName: 'gpt-5' } });
    expect(gatewayGrantDigests(spec).routesDigest).to.equal(grantDigest(['openai:gpt-5']));
    expect(grantedRoutes(spec)).to.deep.equal({
      routes: [{ provider: 'openai', model: 'gpt-5', modelKey: 'm-default', modelName: 'gpt-5', pays: 'org' }],
      engine: 'opencode',
    });
  });

  it("AD-2: an engine run reaches the org's configured model on the org's account, though the actor stored a key of their own", async () => {
    for (const engineProfile of [ENGINE, { ...ENGINE, accountKinds: ['org_api_key', 'user_api_key'] }]) {
      const { spec, run } = await admit({ models: [], engineProfile } as Parameters<typeof agentDefinition>[0]);
      expect(spec.model.policy).to.deep.equal([
        { provider: 'openai', model: 'gpt-5', access: { route: 'gateway', accountKind: 'org_api_key', pays: 'org' } },
      ]);
      expect((await store.readClaimSubject(run.orgId, run.runId))?.modelAccess ?? null).to.equal(null);
    }
  });

  it("AD-3: a key named by the request, the actor's own or another's, is refused account_missing before anything is written", async () => {
    for (const accountId of [OWN_KEY, 'users/user-2/openAI']) {
      const error = await refusal({ models: [], engineProfile: ENGINE }, { accountId, modelName: 'gpt-5' });
      expect(accessCode(error)).to.deep.equal(['account_missing', null]);
      expect((transportError(error) as { statusCode: number }).statusCode).to.equal(403);
    }
  });

  it('AD-4: an account that does not exist, was removed, or is not an account id is refused account_missing', async () => {
    expect(accessCode(await refusal({ models: [], engineProfile: ENGINE }, { accountId: 'users/user-1/anthropic', modelName: 'x' })))
      .to.deep.equal(['account_missing', null]);
    expect(accessCode(await refusal({ models: [], engineProfile: ENGINE }, { accountId: '../etc/passwd' })))
      .to.deep.equal(['account_missing', null]);
  });

  it('AD-6: a row the org switched off, a combination no row allows, release data that does not verify', async () => {
    await policies.write('org-1', { disabledRules: ['openai-api-key-org'], consumerPlans: false }, 'p_admin');
    const off = await refusal({ models: [], engineProfile: ENGINE });
    expect(accessCode(off)).to.deep.equal(['org_policy_refused', 'openai-api-key-org']);
    await policies.write('org-1', { disabledRules: [], consumerPlans: false }, 'p_admin');
    expect((await admit({ models: [], engineProfile: ENGINE })).spec.model.policy[0]?.access?.accountKind).to.equal('org_api_key');

    models.entries = [{ provider: 'ollama', modelKey: 'm-local', modelNames: ['llama'], isDefault: true }];
    expect(accessCode(await refusal({ models: [], engineProfile: ENGINE }))).to.deep.equal(['vendor_rule_missing', null]);

    options = {
      rules: () => {
        throw new ModelAccessRefusal('vendor_rules_invalid', 'tampered', null, 'bad_signature');
      },
    };
    const invalid = await refusal({ models: [], engineProfile: ENGINE });
    expect(accessCode(invalid)).to.deep.equal(['vendor_rules_invalid', null]);
    expect((transportError(invalid) as { statusCode: number }).statusCode).to.equal(503);
  });

  it('AD-6: an org policy store that cannot be read refuses (503), never reads as nothing narrowed', async () => {
    options = { policies: { read: () => Promise.reject(new Error('kv down')) } };
    const error = await refusal({ models: [], engineProfile: ENGINE });
    expect(accessCode(error)).to.deep.equal(['access_policy_unavailable', null]);
  });

  it('AD-7: a loop run naming a model account is refused account_requires_engine', async () => {
    expect(accessCode(await refusal({ models: [] }, { accountId: OWN_KEY, modelName: 'gpt-5' })))
      .to.deep.equal(['account_requires_engine', null]);
  });

  it('AD-8: a loop run without an account pins its route as before A1 (no access, no concrete lookup)', async () => {
    models.down = true;
    const { spec, run } = await admit({ models: [] });
    expect(spec.model.policy).to.deep.equal([{ provider: 'pipeshub', model: 'org-default' }]);
    expect((await store.readClaimSubject(run.orgId, run.runId))?.modelAccess ?? null).to.equal(null);
    expect(grantedRoutes(spec).engine).to.equal('loop');
  });

  it("AD-9: an org ceiling whose accountKinds exclude the org's account refuses on that dimension; no other account is reached for", async () => {
    await ceilings.write('org-1', { accountKinds: ['user_api_key'] } as EngineProfileNarrowing, 'p_admin');
    const error = await refusal({ models: [], engineProfile: ENGINE });
    if (!(error instanceof HarnessEngineProfileRefusedError)) throw error;
    expect(error.dimension).to.equal('accountKinds');
    await ceilings.write('org-1', { accountKinds: ['org_api_key'] } as EngineProfileNarrowing, 'p_admin');
    expect((await admit({ models: [], engineProfile: ENGINE })).spec.model.policy[0]?.access?.accountKind).to.equal('org_api_key');
  });

  it('AD-12: vendor-direct rows and personal-key rows are reported as not built but the Claude plan row (SIGNIN, VR-10)', async () => {
    const effective = await new ModelAccessService({ offering: 'oss', policies, models }).effective('org-1');
    const vendorDirect = effective.rules.filter(
      (rule) => rule.route !== 'gateway' && rule.id !== 'anthropic-claude-plan-claude-code',
    );
    expect(vendorDirect.length).to.be.greaterThan(0);
    expect(vendorDirect.every((rule) => !rule.built)).to.equal(true);
    expect(effective.rules.find((rule) => rule.id === 'anthropic-claude-plan-claude-code')?.built).to.equal(true);
    const gateway = effective.rules.filter((rule) => rule.route === 'gateway');
    expect(gateway.filter((rule) => rule.accountKind !== 'user_api_key').every((rule) => rule.built)).to.equal(true);
    expect(gateway.filter((rule) => rule.accountKind === 'user_api_key').map((rule) => rule.built)).to.not.include(true);
  });

  it("AD-13: the org cloud account the agent's editor pinned is pinned as org_cloud, paid by the org; a member names no other", async () => {
    models.entries = [...models.entries, { provider: 'azureOpenAI', modelKey: 'm-azure', modelNames: ['gpt-5'] }];
    const azure = { modelKey: 'm-azure', modelName: 'gpt-5', provider: 'azureOpenAI', isReasoning: false };
    // SEC-C-02: an agent that pins no cloud account lets nobody name one.
    expect(accessCode(await refusal({ models: [], engineProfile: ENGINE }, { accountId: 'cloud/acc-1', modelName: 'gpt-5' })))
      .to.deep.equal(['account_not_owned', null]);
    const { spec } = await admit(
      { models: [azure], engineProfile: ENGINE, modelAccountId: 'cloud/acc-1' },
      { accountId: 'cloud/acc-1', modelKey: 'm-azure', modelName: 'gpt-5' },
    );
    expect(spec.model.policy[0]).to.deep.equal({
      provider: 'azureopenai',
      model: 'gpt-5',
      access: { route: 'gateway', accountKind: 'org_cloud', accountId: 'cloud/acc-1', pays: 'org' },
    });
  });

  it('OF-1: the offering flag (Node): unset or blank is managed; an unknown value refuses to start', () => {
    expect(offeringFromEnv({})).to.equal('managed');
    expect(offeringFromEnv({ AGENT_HARNESS_OFFERING: '  ' })).to.equal('managed');
    expect(offeringFromEnv({ AGENT_HARNESS_OFFERING: ' OSS ' })).to.equal('oss');
    expect(offeringFromEnv({ AGENT_HARNESS_OFFERING: 'Managed' })).to.equal('managed');
    expect(() => offeringFromEnv({ AGENT_HARNESS_OFFERING: 'enterprise' })).to.throw(/AGENT_HARNESS_OFFERING/);
  });
});
