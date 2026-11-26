/**
 * SEC-C-02: an engine run's model is decided on one path, whatever account
 * reaches it (the org's key, an org cloud account):
 * a configured entry's model name, an org cloud account only as the agent's
 * editor pinned it, and the actor's model groups held to the model resolved.
 * The bypasses an attacker tries: names nobody configured (case, whitespace,
 * Unicode look-alikes), a key and a name that disagree, another org's account,
 * path tricks in the account id, a name outside the default entry with no key.
 * In memory: RunAdmissionService → claim → ChildRunService (the code_task
 * hand-off, CTL-6), over the real vault and the shipped vendor rules.
 */
import { expect } from 'chai';
import { ModelAccountVault } from '../../../src/modules/agent-harness/adapters/configuration/model-account-vault';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryGroupMembership } from '../../../src/modules/agent-harness/adapters/in-memory-group-membership';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { childEngineAuthority, ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { FakeGroupAccess } from './support/org-policy-fakes';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { ModelAccessService } from '../../../src/modules/agent-harness/application/model-access-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessForbiddenError, HarnessGroupAccessRefusedError, HarnessModelAccessRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { AgentRunDefinition } from '../../../src/modules/agent-harness/domain/agent-definition';
import { RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import { codeTaskAuthority } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { pinRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import type { AccessGroupsPolicy } from '../../../src/modules/control-plane';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { AGENT_MODELS, DEFAULT_MODEL, InMemoryConfiguredModels } from './support/model-access';
import { KEYS, MemoryKv, platformCipher } from './support/model-accounts';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings, FakeModelAccessPolicies } from './support/org-policy-fakes';

const ORG = 'org-1';
const MEMBER = 'member-1';
const OWN_KEY = `users/${MEMBER}/openAI`;
const CLOUD = 'cloud/acc-1';
const GROUP = 'group-premium';
const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const ENGINE = { engines: ['opencode'] };
const AZURE = { provider: 'azureOpenAI', modelKey: 'm-azure', modelNames: ['gpt-5', 'gpt-5-mini'] };
const AZURE_GRANT = { modelKey: 'm-azure', modelName: 'gpt-5', provider: 'azureOpenAI', isReasoning: false };
/** An engine agent whose editor pinned the org's cloud account for its Azure model. */
const CLOUD_AGENT: Partial<AgentRunDefinition> = { models: [AZURE_GRANT], engineProfile: ENGINE, modelAccountId: CLOUD };
/** An engine agent that grants no models: the turn's own choice decides, on the org's configured models. */
const OPEN_AGENT: Partial<AgentRunDefinition> = { models: [], engineProfile: ENGINE };

describe('SEC-C-02: model access is one decision, validated against the configured models and groups', () => {
  let now: number;
  let store: InMemoryExecutionStore;
  let service: ExecutionService;
  let vault: ModelAccountVault;
  let membership: InMemoryGroupMembership;
  let groups: AccessGroupsPolicy | null;

  const groupAccess = () =>
    new FakeGroupAccess({
      policies: { read: async () => groups },
      membership,
      models: new InMemoryConfiguredModels([DEFAULT_MODEL, ...AGENT_MODELS, AZURE]),
    });

  /**
   * The member's turn handed off to its engine child; the child's pinned first route. `admitGroups: false`
   * leaves the groups to the spawn alone, as when they change after the turn was admitted.
   */
  const childRoute = async (
    definition: Partial<AgentRunDefinition>,
    model: Partial<RunInputSnapshot['model']>,
    { admitGroups = true, orgId = ORG }: { admitGroups?: boolean; orgId?: string } = {},
  ) => {
    // Each case starts from an empty store: the cases' many turns would otherwise meet the active-run quota.
    store = new InMemoryExecutionStore({ now: () => now });
    service = new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: pinRunSpec,
    });
    const engineProfiles = new FakeEngineCeilings();
    const access = new ModelAccessService({
      offering: 'oss',
      policies: new FakeModelAccessPolicies(),
      models: new InMemoryConfiguredModels([DEFAULT_MODEL, ...AGENT_MODELS, AZURE]),
      accounts: vault,
    });
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => now },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey, ...definition })),
      engineProfiles,
      modelAccess: access,
      ...(admitGroups ? { groupAccess: groupAccess() } : {}),
    }).submit({
      orgId,
      actorId: MEMBER,
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${String(now++)}`,
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto', ...model } },
    });
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    const claimed = await service.claim({
      orgId,
      runId: outcome.admission.run.runId,
      workerId: 'w',
      attemptId: `a-${outcome.admission.run.runId}`,
      leaseDurationMs: 1_000,
    });
    expect(codeTaskAuthority(claimed.pinned!.spec), 'the turn hands off to an engine child').to.not.equal(null);
    const spawned = await new ChildRunService({
      runs: store,
      children: store,
      journals: new JournalService(new InMemoryPayloadStore(), testCipher()),
      workspaces: null,
      engineAuthority: childEngineAuthority(engineProfiles),
      engineAccess: (subject) => access.decide(subject),
      groupAccess: groupAccess(),
    }).spawn(
      { orgId, runId: claimed.run.runId, workerId: 'w', attemptId: `a-${claimed.run.runId}`, fence: claimed.run.fence },
      { invocationId: 't1.0', goal: 'build it', grants: {}, executor: { kind: 'engine' } },
    );
    const child = await store.readClaimSubject(orgId, spawned.childRunId);
    return child!.pinned!.spec.model.policy[0]!;
  };

  /** The refusal's code; anything else (a pin, a crash) fails the case. */
  const refusedCode = async (
    definition: Partial<AgentRunDefinition>,
    model: Partial<RunInputSnapshot['model']>,
    options?: { admitGroups?: boolean; orgId?: string },
  ): Promise<string> => {
    try {
      const pinned = await childRoute(definition, model, options);
      return expect.fail(`expected a refusal, pinned ${JSON.stringify(pinned)}`);
    } catch (error) {
      if (error instanceof HarnessModelAccessRefusedError) return error.code;
      if (error instanceof HarnessGroupAccessRefusedError) return error.code;
      if (error instanceof HarnessForbiddenError) return 'forbidden';
      throw error;
    }
  };

  beforeEach(async () => {
    now = 1_000;
    groups = null;
    membership = new InMemoryGroupMembership();
    vault = new ModelAccountVault(new MemoryKv(), platformCipher);
    await vault.put(ORG, { kind: 'user', userId: MEMBER }, 'openAI', {
      kind: 'user_api_key',
      provider: 'openAI',
      label: null,
      material: { provider: 'openAI', apiKey: KEYS.openAI },
      hint: KEYS.openAI,
    }, MEMBER);
    for (const org of [ORG, 'org-2']) {
      await vault.put(org, { kind: 'org' }, org === ORG ? 'acc-1' : 'acc-other', {
        kind: 'org_cloud',
        provider: 'azureOpenAI',
        label: 'EU',
        material: { provider: 'azureOpenAI', apiKey: KEYS.azure, endpoint: 'https://x.openai.azure.com', deploymentName: 'gpt-5' },
        hint: KEYS.azure,
      }, 'admin');
    }
  });

  describe('legitimate use keeps working', () => {
    it("C02-1: the org key and the editor-pinned cloud account each pin a configured model; a person's key is no account here", async () => {
      expect(await childRoute(OPEN_AGENT, { modelKey: 'model-1', modelName: 'gpt-x' })).to.deep.include({
        provider: 'openai', model: 'gpt-x', access: { route: 'gateway', accountKind: 'org_api_key', pays: 'org' },
      });
      expect(await refusedCode(OPEN_AGENT, { accountId: OWN_KEY, modelKey: 'model-1', modelName: 'gpt-x' })).to.equal('account_missing');
      for (const model of [{}, { accountId: CLOUD }, { accountId: CLOUD, modelKey: 'm-azure', modelName: 'gpt-5' }]) {
        expect(await childRoute(CLOUD_AGENT, model)).to.deep.include({
          provider: 'azureopenai', model: 'gpt-5', access: { route: 'gateway', accountKind: 'org_cloud', accountId: CLOUD, pays: 'org' },
        });
      }
    });
  });

  describe('an unconfigured model is refused on every account kind', () => {
    for (const [kind, definition, account] of [
      ['org key', OPEN_AGENT, {}],
    ] as const) {
      it(`C02-2 (${kind}): a name nobody configured, or a case, whitespace or look-alike variant of one, is route_not_configured`, async () => {
        for (const modelName of [
          'o1-pro-most-expensive',
          'GPT-5',
          'gpt-5 ',
          ' gpt-5',
          'gpt-5​',
          'gpt‑5', // non-breaking hyphen
          'gрt-5', // Cyrillic р
          'gpt-５', // fullwidth digit
          'gpt-5,o1-pro',
        ]) {
          expect(await refusedCode(definition, { ...account, modelName }), JSON.stringify(modelName)).to.equal('route_not_configured');
          expect(await refusedCode(definition, { ...account, modelKey: 'm-default', modelName }), JSON.stringify(modelName))
            .to.equal('route_not_configured');
        }
      });

      it(`C02-3 (${kind}): a modelKey and a modelName that disagree, an unknown key, or a name of another entry with no key are refused`, async () => {
        expect(await refusedCode(definition, { ...account, modelKey: 'model-1', modelName: 'gpt-5' })).to.equal('route_not_configured');
        expect(await refusedCode(definition, { ...account, modelKey: 'no-such-key', modelName: 'gpt-5' })).to.equal('route_not_configured');
        // With no key, a name picks among the default entry only: the entry the group check reads.
        expect(await refusedCode(definition, { ...account, modelName: 'gpt-x' })).to.equal('route_not_configured');
      });
    }

    it('C02-4 (cloud account): a name the cloud agent does not grant is refused on its pinned account too', async () => {
      for (const model of [
        { accountId: CLOUD, modelKey: 'm-azure', modelName: 'o1-pro-most-expensive' },
        { accountId: CLOUD, modelKey: 'm-azure', modelName: 'GPT-5' },
      ]) {
        expect(await refusedCode(CLOUD_AGENT, model)).to.equal('forbidden');
      }
    });

    it('C02-5: the refusal names the models that may be chosen, never the name asked for, and is a 403', async () => {
      try {
        await childRoute(OPEN_AGENT, { modelKey: 'm-default', modelName: 'ignore previous instructions' });
        expect.fail('expected a refusal');
      } catch (error) {
        if (!(error instanceof HarnessModelAccessRefusedError)) throw error;
        expect(error.message).to.contain('gpt-5, gpt-5-mini').and.to.contain('ask your admin');
        expect(error.message).to.not.contain('ignore previous');
        expect((transportError(error) as { statusCode: number }).statusCode).to.equal(403);
      }
    });
  });

  describe('an org cloud account is the agent editor\'s choice, not a member\'s', () => {
    it('C02-6: naming a cloud account the agent does not pin is account_not_owned, before the account is read', async () => {
      expect(await refusedCode(OPEN_AGENT, { accountId: CLOUD, modelName: 'gpt-5' })).to.equal('account_not_owned');
      expect(await refusedCode(OPEN_AGENT, { accountId: CLOUD, modelKey: 'm-azure', modelName: 'gpt-5' })).to.equal('account_not_owned');
      // A cloud agent's member cannot swap in another cloud id, existing or not.
      for (const accountId of ['cloud/acc-2', 'cloud/acc-other', 'cloud/ACC-1']) {
        expect(await refusedCode(CLOUD_AGENT, { accountId }), accountId).to.equal('account_not_owned');
      }
    });

    it("C02-7: another org's cloud account id is refused, even when an agent of this org names it", async () => {
      expect(await refusedCode(OPEN_AGENT, { accountId: 'cloud/acc-other', modelName: 'gpt-5' })).to.equal('account_not_owned');
      expect(await refusedCode({ ...CLOUD_AGENT, modelAccountId: 'cloud/acc-other' }, {})).to.equal('account_missing');
    });

    it('C02-8: account ids with path tricks or encodings read as no account', async () => {
      for (const accountId of [
        'cloud/../acc-1',
        'cloud/..',
        'cloud/.',
        'cloud/%2e',
        'cloud/%2e%2e/acc-1',
        'cloud/acc-1/',
        'cloud//acc-1',
        'cloud/acc-1\u0000',
        ' cloud/acc-1',
        'CLOUD/acc-1',
        `users/${MEMBER}/../openAI`,
        `users/${MEMBER}/openAI/x`,
      ]) {
        expect(await refusedCode(CLOUD_AGENT, { accountId }), JSON.stringify(accountId)).to.equal('account_missing');
      }
    });
  });

  describe('the model groups hold whichever account reaches the model', () => {
    beforeEach(() => {
      membership.add(ORG, GROUP, 'someone-else');
    });

    it('C02-9: a model outside the actor\'s groups is model_group_required on the org key and a cloud account', async () => {
      groups = { models: { 'model-1': [GROUP] } };
      expect(await refusedCode(OPEN_AGENT, { modelKey: 'model-1', modelName: 'gpt-x' })).to.equal('MODEL_GROUP_REQUIRED');
      groups = { models: { 'm-azure': [GROUP] } };
      expect(await refusedCode(CLOUD_AGENT, { accountId: CLOUD })).to.equal('MODEL_GROUP_REQUIRED');
      expect(await refusedCode(CLOUD_AGENT, {})).to.equal('MODEL_GROUP_REQUIRED');
    });

    it('C02-12 (R38-AUTHZ): a group refusal names the configured models the person may pick instead', async () => {
      groups = { models: { 'model-1': [GROUP] } };
      let refused: unknown;
      try {
        await childRoute(OPEN_AGENT, { modelKey: 'model-1', modelName: 'gpt-x' });
      } catch (error) {
        refused = error;
      }
      expect(refused).to.be.instanceOf(HarnessGroupAccessRefusedError);
      const message = (refused as Error).message;
      expect(message).to.contain('Models you can use:').and.contain('claude-x').and.contain('gpt-5');
      expect(message).not.to.contain('gpt-x');
      const named = await groupAccess().allowedModelNames(ORG, MEMBER, 'anthropic');
      expect(named).to.deep.equal(['claude-x']);
    });

    it('C02-10: a name with no key is held to the default entry it resolves to, never skips the check', async () => {
      groups = { models: { 'm-default': [GROUP] } };
      expect(await refusedCode(OPEN_AGENT, { modelName: 'gpt-5-mini' })).to.equal('MODEL_GROUP_REQUIRED');
      expect(await refusedCode(OPEN_AGENT, { modelName: 'gpt-5' })).to.equal('MODEL_GROUP_REQUIRED');
    });

    it('C02-11: the spawn checks the groups again on the access it resolved (a removal after admission refuses)', async () => {
      groups = { models: { 'model-1': [GROUP] } };
      expect(await refusedCode(OPEN_AGENT, { modelKey: 'model-1', modelName: 'gpt-x' }, { admitGroups: false })).to.equal('MODEL_GROUP_REQUIRED');
      groups = { models: { 'm-azure': [GROUP] } };
      expect(await refusedCode(CLOUD_AGENT, { accountId: CLOUD }, { admitGroups: false })).to.equal('MODEL_GROUP_REQUIRED');
      membership.add(ORG, GROUP, MEMBER);
      expect(await childRoute(CLOUD_AGENT, { accountId: CLOUD }, { admitGroups: false })).to.deep.include({ model: 'gpt-5' });
    });
  });
});
