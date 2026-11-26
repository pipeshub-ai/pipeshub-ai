/**
 * AG1 AA: an engine agent's engine work uses the account its profile allows
 * without the client naming one. Since CTL-6 the turn runs on the loop and the
 * account is chosen when it hands off to its code_task engine child, from the
 * actor's identity, the agent definition and the pinned profile; the child's
 * spec pins what was chosen.
 * In memory: RunAdmissionService → InMemoryExecutionStore → claim; accounts in
 * the real vault over an in-memory key-value store; the shipped vendor rules.
 */
import { expect } from 'chai';
import { ModelAccountVault } from '../../../src/modules/agent-harness/adapters/configuration/model-account-vault';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { ModelAccessService } from '../../../src/modules/agent-harness/application/model-access-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { childEngineAuthority, ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { codeTaskAuthority } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { agentRunDefinitionSchema } from '../../../src/modules/agent-harness/domain/agent-definition';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessEngineProfileRefusedError, HarnessModelAccessRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import { pinRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { AGENT_MODELS, DEFAULT_MODEL, InMemoryConfiguredModels } from './support/model-access';
import { KEYS, MemoryKv, platformCipher } from './support/model-accounts';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings, FakeModelAccessPolicies } from './support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const OWN_KEY = 'users/user-1/openAI';
const AZURE_MODEL = { modelKey: 'm-azure', modelName: 'gpt-5', provider: 'azureOpenAI', isReasoning: true };
const engine = (accountKinds?: string[]) => ({
  engines: ['opencode'],
  ...(accountKinds === undefined ? {} : { accountKinds }),
});

describe('AG1 AA: a coder agent’s engine child gets its model account from the profile', () => {
  let now: number;
  let store: InMemoryExecutionStore;
  let service: ExecutionService;
  let vault: ModelAccountVault;
  let reads: string[];

  const admit = async (
    definition: Parameters<typeof agentDefinition>[0],
    model: Partial<RunInputSnapshot['model']> = {},
  ) => {
    const engineProfiles = new FakeEngineCeilings();
    const access = new ModelAccessService({
      offering: 'oss',
      policies: new FakeModelAccessPolicies(),
      models: new InMemoryConfiguredModels([DEFAULT_MODEL, ...AGENT_MODELS, { ...AZURE_MODEL, modelNames: ['gpt-5'] }]),
      accounts: {
        list: (...args) => vault.list(...args),
        get: (orgId, owner, id) => {
          reads.push(`${owner.kind}:${id}`);
          return vault.get(orgId, owner, id);
        },
        put: (...args) => vault.put(...args),
        revoke: (...args) => vault.revoke(...args),
      },
    });
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => now },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey, ...definition })),
      engineProfiles,
      modelAccess: access,
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${String(now++)}`,
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto', ...model } },
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
      const subject = await store.readClaimSubject('org-1', outcome.admission.run.runId);
      return { spec: claimed.pinned!.spec, stored: subject?.modelAccess ?? null };
    }
    // The turn hands off: its engine child is where the account is chosen and pinned.
    const spawned = await new ChildRunService({
      runs: store,
      children: store,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      workspaces: null,
      engineAuthority: childEngineAuthority(engineProfiles),
      engineAccess: (subject) => access.decide(subject),
    }).spawn(
      { orgId: 'org-1', runId: claimed.run.runId, workerId: 'w', attemptId: `a-${claimed.run.runId}`, fence: claimed.run.fence },
      { invocationId: 't1.0', goal: 'build it', grants: {}, executor: { kind: 'engine' } },
    );
    const child = await store.readClaimSubject('org-1', spawned.childRunId);
    // A child's access is pinned in its spec when it is admitted; there is nothing else to store.
    const spec = child!.pinned!.spec;
    return { spec, stored: { access: spec.model.policy[0]?.access } };
  };

  const refused = async (
    definition: Parameters<typeof agentDefinition>[0],
    model: Partial<RunInputSnapshot['model']> = {},
  ): Promise<[string, number]> => {
    const runs = (store as unknown as { runs: Map<string, { parent?: unknown }> }).runs;
    const children = () => [...runs.values()].filter((run) => run.parent != null).length;
    const before = children();
    try {
      await admit(definition, model);
    } catch (error) {
      // The turn ran; its engine child was refused before anything of it was written.
      expect(children(), 'no child written').to.equal(before);
      if (!(error instanceof HarnessModelAccessRefusedError)) throw error;
      return [error.code, (transportError(error) as { statusCode: number }).statusCode];
    }
    return expect.fail('expected a refusal');
  };

  const policy = async (definition: Parameters<typeof agentDefinition>[0]) =>
    (await admit(definition)).spec.model.policy[0];

  beforeEach(async () => {
    now = 1_000;
    reads = [];
    store = new InMemoryExecutionStore({ now: () => now });
    service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: pinRunSpec,
    });
    vault = new ModelAccountVault(new MemoryKv(), platformCipher);
    for (const [userId, key] of [['user-1', KEYS.openAI], ['user-2', KEYS.openAIRotated]] as const) {
      await vault.put('org-1', { kind: 'user', userId }, 'openAI', {
        kind: 'user_api_key',
        provider: 'openAI',
        label: null,
        material: { provider: 'openAI', apiKey: key },
        hint: key,
      }, userId);
    }
    await vault.put('org-1', { kind: 'org' }, 'acc-1', {
      kind: 'org_cloud',
      provider: 'azureOpenAI',
      label: 'EU',
      material: { provider: 'azureOpenAI', apiKey: KEYS.azure, endpoint: 'https://x.openai.azure.com', deploymentName: 'gpt-5' },
      hint: KEYS.azure,
    }, 'admin');
  });

  it('AA-1: an agent whose profile admits the org key uses it; no account is read', async () => {
    expect(await policy({ engineProfile: engine() })).to.deep.equal({
      provider: 'openai',
      model: 'gpt-x',
      access: { route: 'gateway', accountKind: 'org_api_key', pays: 'org' },
    });
    expect(await policy({ engineProfile: engine(['org_api_key', 'user_api_key']) }))
      .to.have.nested.property('access.accountKind', 'org_api_key');
    expect(reads).to.deep.equal([]);
  });

  it("AA-2: an agent narrowed to personal keys is refused on accountKinds, never run on someone's key; no key is read", async () => {
    let error: unknown = null;
    try {
      await admit({ engineProfile: engine(['user_api_key']) });
    } catch (caught) {
      error = caught;
    }
    expect(error).to.be.instanceOf(HarnessEngineProfileRefusedError);
    expect((error as HarnessEngineProfileRefusedError).dimension).to.equal('accountKinds');
    expect(reads).to.deep.equal([]);
  });

  it("AA-5: an org-cloud agent runs on the cloud account its editor pinned, paid by the org", async () => {
    expect(await policy({ engineProfile: engine(['org_cloud']), models: [AZURE_MODEL], modelAccountId: 'cloud/acc-1' }))
      .to.deep.equal({
        provider: 'azureopenai',
        model: 'gpt-5',
        access: { route: 'gateway', accountKind: 'org_cloud', accountId: 'cloud/acc-1', pays: 'org' },
      });
    expect(await refused({ engineProfile: engine(['org_cloud']), modelAccountId: 'cloud/acc-1' }))
      .to.deep.equal(['account_provider_mismatch', 403]);
    expect(await refused({ engineProfile: engine(['org_cloud']), models: [AZURE_MODEL], modelAccountId: 'cloud/gone' }))
      .to.deep.equal(['account_missing', 403]);
  });

  it('AA-6: an org-cloud agent without a pinned account is refused account_missing', async () => {
    expect(await refused({ engineProfile: engine(['org_cloud']), models: [AZURE_MODEL] }))
      .to.deep.equal(['account_missing', 403]);
  });

  it("AA-7: a client naming a person's key, the actor's own or another's, is refused account_missing, whatever the agent allows", async () => {
    for (const kinds of [undefined, ['user_api_key']]) {
      for (const accountId of [OWN_KEY, 'users/user-2/openAI']) {
        expect(await refused({ engineProfile: engine(kinds) }, { accountId })).to.deep.equal(['account_missing', 403]);
      }
    }
    expect(reads).to.deep.equal([]);
  });

  it("AA-9: a definition pinning a person's key is refused account_not_owned, even the actor's own", async () => {
    for (const pinned of ['users/user-2/openAI', OWN_KEY, 'not-an-account']) {
      expect(await refused({ engineProfile: engine(), modelAccountId: pinned }))
        .to.deep.equal(['account_not_owned', 403]);
    }
  });

  it('AA-10: a loop agent reads no account and pins no access', async () => {
    const { spec, stored } = await admit({ models: [] });
    expect(spec.model.policy).to.deep.equal([{ provider: 'pipeshub', model: 'org-default' }]);
    expect(stored).to.equal(null);
    expect(reads).to.deep.equal([]);
  });

  it('RD-3: the agent definition carries the pinned account; an older authority without it still parses', () => {
    const parsed = agentRunDefinitionSchema.parse({ ...agentDefinition(), modelAccountId: 'cloud/acc-1' });
    expect(parsed.modelAccountId).to.equal('cloud/acc-1');
    expect(agentRunDefinitionSchema.parse(agentDefinition()).modelAccountId).to.equal(undefined);
  });
});
