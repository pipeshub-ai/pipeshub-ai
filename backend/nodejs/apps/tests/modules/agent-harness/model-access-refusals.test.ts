/**
 * A1 / MG-15 / SIGNIN refusals of `ModelAccessService` that admission and the settings pages rely on: a store
 * that cannot be read refuses with a code the route maps (never a silent "no"), a deployment that composes no
 * models or accounts refuses rather than guessing, and the region pins decide each model choice's region.
 */
import { expect } from 'chai';
import { resolveEngineProfile, type PinnedEngineProfile } from '../../../src/modules/agent-harness/domain/engine-profiles';
import { HarnessEngineProfileRefusedError, HarnessModelAccessRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { ModelAccessRefusal } from '../../../src/modules/agent-harness/domain/model-access';
import type { AccessSubject } from '../../../src/modules/agent-harness/application/model-access-service';
import type { ModelPlacementSource } from '../../../src/modules/agent-harness/ports/model-access';
import { agentDefinition } from './support/agent-definition';
import { InMemoryConfiguredModels, testModelAccess } from './support/model-access';

const profileFor = (agent: Record<string, unknown>): PinnedEngineProfile => {
  const decision = resolveEngineProfile({ agent });
  if (decision.profile === null) throw new Error(`no profile: ${JSON.stringify(decision.refusal)}`);
  return decision.profile;
};

const OPENCODE = profileFor({ engines: ['opencode'] });

const subject = (overrides: Partial<AccessSubject> = {}): AccessSubject => ({
  orgId: 'org-1',
  actorId: 'user-1',
  definition: null,
  model: { chatMode: 'auto' } as AccessSubject['model'],
  engineProfile: OPENCODE,
  ...overrides,
});

async function refusal(work: Promise<unknown>): Promise<HarnessModelAccessRefusedError> {
  try {
    await work;
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessModelAccessRefusedError);
    return error as HarnessModelAccessRefusedError;
  }
  throw new Error('expected a refusal');
}

describe('model access: stores that cannot be read, or are not composed, refuse', () => {
  it('an engine run where no configured models can be read is refused route_not_configured', async () => {
    const access = testModelAccess({ models: undefined });
    const error = await refusal(access.decide(subject()));
    expect(error.code).to.equal('route_not_configured');
    expect(error.message).to.equal('The configured models cannot be read here');
  });

  it('an engine run whose configured models are down is refused, not run on a guess', async () => {
    const models = new InMemoryConfiguredModels();
    models.down = true;
    const error = await refusal(testModelAccess({ models }).decide(subject()));
    expect(error.code).to.equal('route_not_configured');
    expect(error.message).to.equal('The configured models could not be read');
  });

  it('the engine pickers’ refusals: configured models that cannot be read refuse the whole answer', async () => {
    const models = new InMemoryConfiguredModels();
    models.down = true;
    const error = await refusal(testModelAccess({ models }).engineRefusals('org-1', ['opencode']));
    expect(error.code).to.equal('route_not_configured');
  });

  it('the engine pickers’ refusals: an org with no model configured names that for every engine', async () => {
    const refusals = await testModelAccess({ models: new InMemoryConfiguredModels([]) }).engineRefusals('org-1', [
      'opencode',
      'codex',
    ]);
    expect([...refusals.keys()]).to.deep.equal(['opencode', 'codex']);
    for (const value of refusals.values()) expect(value?.code).to.equal('route_not_configured');
  });

  it('an agent pinned to an org cloud account where no accounts are composed is refused account_missing', async () => {
    const access = testModelAccess({ accounts: undefined });
    const error = await refusal(
      access.decide(subject({ definition: agentDefinition({ modelAccountId: 'cloud/azure-1' } as never) })),
    );
    expect(error.code).to.equal('account_missing');
    expect(error.message).to.equal('Model accounts are not configured here');
  });

  it('a profile that admits only an org cloud account, with none chosen for the agent, is refused account_missing', async () => {
    const access = testModelAccess();
    const error = await refusal(
      access.decide(subject({ engineProfile: { ...OPENCODE, accountKinds: ['org_cloud'] } as PinnedEngineProfile })),
    );
    expect(error.code).to.equal('account_missing');
    expect(error.message).to.match(/organization cloud account/);
  });

  it('a model the org no longer configures is refused route_not_configured', async () => {
    const error = await refusal(
      testModelAccess().decide(
        subject({ model: { chatMode: 'auto', modelKey: 'm-default', modelName: 'gpt-4-gone' } as AccessSubject['model'] }),
      ),
    );
    expect(error.code).to.equal('route_not_configured');
    // SEC-C-02: the refusal names what may be chosen instead (the entry's configured names), never the name asked.
    expect(error.message).to.equal(
      'The chosen model is not set up for this organization. The models you can choose here are: gpt-5, gpt-5-mini. Pick one of them, or ask your admin to add it',
    );
  });

  it('a profile admitting no kind the org can provide is refused by the profile, on the request layer', async () => {
    try {
      await testModelAccess().decide(
        subject({ engineProfile: { ...OPENCODE, accountKinds: ['user_plan_sandbox_login'] } as PinnedEngineProfile }),
      );
      expect.fail('expected a refusal');
    } catch (error) {
      expect(error).to.be.instanceOf(HarnessEngineProfileRefusedError);
      expect(error).to.include({ code: 'ENGINE_PROFILE_WIDENING', layer: 'request' });
    }
  });

  it('no org narrowing bound: the release default, nothing switched off and consumer plans off', async () => {
    const access = testModelAccess({ policies: undefined });
    expect(await access.policy('org-1')).to.deep.equal({ disabledRules: [], consumerPlans: false });
    const refused = (await access.effective('org-1')).rules.filter((rule) => rule.outcome === 'org_policy_refused');
    expect(refused.every((rule) => rule.consumer)).to.equal(true);
    expect(await access.policyRevision('org-1')).to.equal(null);
  });

  it('the release rows are read without the org narrowing: one that cannot be read does not hide them', async () => {
    const access = testModelAccess({ policies: { read: () => Promise.reject(new Error('down')) } });
    expect(access.verifiedRules().rules.length).to.be.greaterThan(0);
    const error = await refusal(access.effective('org-1'));
    expect(error.code).to.equal('access_policy_unavailable');
    const bad = testModelAccess({
      rules: () => {
        throw new ModelAccessRefusal('vendor_rules_invalid', 'the signature does not verify');
      },
    });
    expect(() => bad.verifiedRules()).to.throw(HarnessModelAccessRefusedError);
  });

  it('vendor rules that fail their check refuse with their own code; any other fault is not dressed up as one', async () => {
    const bad = testModelAccess({
      rules: () => {
        throw new ModelAccessRefusal('vendor_rules_invalid', 'the signature does not verify');
      },
    });
    const error = await refusal(bad.effective('org-1'));
    expect(error.code).to.equal('vendor_rules_invalid');
    const broken = testModelAccess({
      rules: () => {
        throw new TypeError('bug');
      },
    });
    try {
      await broken.effective('org-1');
      expect.fail('expected the fault');
    } catch (fault) {
      expect(fault).to.be.instanceOf(TypeError);
    }
  });
});

describe('SIGNIN / SIGNGAP: the person’s own Claude plan', () => {
  const CLAUDE = profileFor({ engines: ['claude_code'] });

  it('a sign-in choice that cannot be read refuses the turn instead of falling back to the org’s account', async () => {
    const access = testModelAccess({
      signIns: () => ({
        usable: async () => {
          throw new Error('kv down');
        },
      }),
    });
    const error = await refusal(access.decide(subject({ engineProfile: CLAUDE })));
    expect(error.code).to.equal('access_policy_unavailable');
    expect(error.message).to.match(/own Claude account/);
  });

  it('a probe for workers that carry a sign-in that fails answers no; an absent one assumes yes', async () => {
    expect(
      await testModelAccess({
        signInsCarried: async () => {
          throw new Error('registry down');
        },
      }).signInsCarried(),
    ).to.equal(false);
    expect(await testModelAccess().signInsCarried()).to.equal(true);
  });
});

describe('model choices show what a bound ModelPlacementSource adds, beside their own members', () => {
  const extra = (
    view: Record<string, unknown>,
    shown: Record<string, Record<string, unknown>>,
  ): ModelPlacementSource => ({
    choices: async () => ({ view, of: (_entry, modelName) => shown[modelName] }),
  });

  it('unbound, the choices carry nothing more', async () => {
    const choices = await testModelAccess().modelChoices('org-1');
    expect(Object.keys(choices ?? {}).sort()).to.deep.equal(['modelChoices', 'roleCostCeilings']);
    for (const choice of choices?.modelChoices ?? []) expect(choice).to.not.have.property('editionNote');
  });

  it('bound, each choice carries what the source says of it and the view what it adds; never in place of a member', async () => {
    const models = new InMemoryConfiguredModels([
      { provider: 'litellm', modelKey: 'm-proxy', modelNames: ['proxied', 'other'], location: { endpoint: 'https://proxy.example.com' } },
    ]);
    const access = testModelAccess({
      models,
      placement: () =>
        extra(
          { editionList: ['a'], roleCostCeilings: { default: 0 } },
          { proxied: { editionNote: 'n-1', allowed: true } },
        ),
    });
    const choices = (await access.modelChoices('org-1', { routes: [] })) as Record<string, unknown> | null;
    expect(choices?.['editionList']).to.deep.equal(['a']);
    expect(choices?.['roleCostCeilings']).to.deep.equal({});
    const [proxied, other] = (choices?.['modelChoices'] ?? []) as Record<string, unknown>[];
    expect(proxied).to.include({ modelName: 'proxied', editionNote: 'n-1', allowed: false });
    expect(other).to.not.have.property('editionNote');
  });

  it('what cannot be read is left out of the choices (a run is still held to its rules)', async () => {
    const access = testModelAccess({
      placement: () => ({
        choices: async () => {
          throw new Error('policy store down');
        },
      }),
    });
    const choices = await access.modelChoices('org-1');
    expect(choices).to.not.equal(null);
    expect(Object.keys(choices ?? {}).sort()).to.deep.equal(['modelChoices', 'roleCostCeilings']);
  });

  it('without a model list there are no choices to offer', async () => {
    const access = testModelAccess({ models: { find: async () => null } });
    expect(await access.modelChoices('org-1')).to.equal(null);
  });
});
