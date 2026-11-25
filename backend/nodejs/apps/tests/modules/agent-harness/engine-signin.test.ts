/**
 * SIGNIN-2 on Node: who may use a person's own Claude account (AD-14..AD-21),
 * the sign-in API's service (SA-1..SA-12), ending it (DC-1..DC-6) and what
 * is never kept or logged (LK-N1..LK-N3). In memory: the user and org
 * records, a relay whose worker side the test plays, the audited control
 * plane, and the real ModelAccessService over the signed vendor rules.
 */
import { expect } from 'chai';
import {
  InMemoryEngineSignIns,
  InMemorySignInRelay,
} from '../../../src/modules/agent-harness/adapters/in-memory-engine-signins';
import {
  EngineSignInService,
  engineSignInEraser,
} from '../../../src/modules/agent-harness/application/engine-signin-service';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { AdmissionRefusal } from '../../../src/modules/agent-harness/application/admission/refusals';
import { ModelGroupStep } from '../../../src/modules/agent-harness/application/admission/policy-steps';
import { FakeGroupAccess, FakeModelAccessPolicies } from './support/org-policy-fakes';
import { ModelAccessService } from '../../../src/modules/agent-harness/application/model-access-service';
import { RELEASE_ENGINE_PROFILES } from '../../../src/modules/agent-harness/domain/engine-profiles';
import type { PinnedEngineProfile } from '../../../src/modules/agent-harness/domain/engine-profiles';
import {
  planAccountId,
  SIGNIN_ATTEMPT,
  SIGNIN_CODE,
  signInRefusal,
} from '../../../src/modules/agent-harness/domain/engine-signin';
import {
  HarnessModelAccessRefusedError,
  HarnessNotFoundError,
  HarnessUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';
import { rowBuilt } from '../../../src/modules/agent-harness/domain/model-access';
import type { SignInRelayAnswer, SignInRelayRequest } from '../../../src/modules/agent-harness/ports/engine-signins';
import { auditedControlPlane } from '../control-plane/support/in-memory';
import { describeEngineSignInStoreContract } from './support/engine-signin-store-contract';
import { DEFAULT_MODEL, InMemoryConfiguredModels, testModelAccess } from './support/model-access';

const ORG = 'org-1';
const USER = 'user-1';
const OTHER = 'user-2';
const ATTEMPT = 'sin_run-1.a1';
const LINK = 'https://claude.com/cai/oauth/authorize?code=true&state=s1';
const CODE = 'SECRETCODE123#s1';
const EMAIL = 'ada@example.com';

const profileOf = (kind: string): PinnedEngineProfile => {
  const found = RELEASE_ENGINE_PROFILES.find((p) => p.engine.kind === kind);
  if (found === undefined) throw new Error(`no release profile for ${kind}`);
  return found;
};

async function refusal(promise: Promise<unknown>): Promise<Error> {
  try {
    await promise;
  } catch (error) {
    return error as Error;
  }
  throw new Error('expected a refusal');
}

const code = (error: Error): string =>
  error instanceof HarnessModelAccessRefusedError ? error.code : error.name;

describe('SIGNIN: who may use a person\'s own Claude account', () => {
  it('AD-14..AD-17: only the owner\'s own web turn in an unshared conversation', () => {
    expect(signInRefusal({ via: 'session', surface: 'web', shared: false })).to.equal(null);
    expect(signInRefusal({ via: 'session', shared: false })).to.equal(null);
    for (const via of ['trigger', 'service', 'oauth_app', 'pat']) {
      expect(signInRefusal({ via, shared: false }), via).to.equal('account_requires_interactive');
    }
    for (const surface of ['slack', 'mcp']) {
      expect(signInRefusal({ via: 'session', surface, shared: false }), surface).to.equal(
        'account_requires_interactive',
      );
    }
    expect(signInRefusal({ via: 'session', surface: 'web', shared: true })).to.equal('account_shared_session');
  });

  it('SA-10: the code and attempt shapes the API accepts', () => {
    expect(SIGNIN_CODE.test(CODE)).to.equal(true);
    for (const bad of ['SECRETCODE123', 'a b#s', 'a#b#c', 'a#', '#b', `a#s\nx`]) {
      expect(SIGNIN_CODE.test(bad), bad).to.equal(false);
    }
    expect(SIGNIN_ATTEMPT.test(ATTEMPT)).to.equal(true);
    expect(SIGNIN_ATTEMPT.test('../x')).to.equal(false);
  });

  it('VR-10: the Claude plan row is built; other vendor-direct rows are not', () => {
    expect(rowBuilt({ id: 'anthropic-claude-plan-claude-code', route: 'vendor_sandbox_login' })).to.equal(true);
    expect(rowBuilt({ id: 'openai-codex-chatgpt-auth-tokens', route: 'vendor_sandbox_login' })).to.equal(false);
    expect(rowBuilt({ id: 'openai-chatgpt-workspace-token', route: 'vendor_injected' })).to.equal(false);
    expect(rowBuilt({ id: 'any', route: 'gateway' })).to.equal(true);
  });
});

describe('SIGNIN: the service', () => {
  let store: InMemoryEngineSignIns;
  let relay: InMemorySignInRelay;
  let access: ModelAccessService;
  let audited: ReturnType<typeof auditedControlPlane>;
  let lines: Array<[string, Record<string, unknown> | undefined]>;
  let service: EngineSignInService;
  let worker: (request: SignInRelayRequest, caller: { userId: string }) => SignInRelayAnswer | null;

  const logger: HarnessLogger = {
    debug: () => undefined,
    info: (event, meta) => void lines.push([event, meta]),
    warn: (event, meta) => void lines.push([event, meta]),
    error: (event, meta) => void lines.push([event, meta]),
  };

  let policies: FakeModelAccessPolicies;
  const consumerPlans = async (on: boolean): Promise<void> => {
    await policies.write(ORG, { ...(await policies.read(ORG)), consumerPlans: on });
  };

  beforeEach(() => {
    lines = [];
    store = new InMemoryEngineSignIns();
    store.addUser(ORG, USER);
    store.addUser(ORG, OTHER);
    store.addUser(ORG, 'svc-1', true);
    worker = (request, caller) => {
      if (caller.userId !== USER) return { ok: false, reasonCode: 'signin_not_owner' };
      if (request.op === 'describe') {
        return { ok: true, ownerId: USER, runId: 'run-1', sessionId: 'ses-1', state: 'waiting', link: LINK, expiresAt: 1_792_000_000_000 };
      }
      if (request.op === 'code') {
        return request.workPlansOnly
          ? { ok: true, ownerId: USER, state: 'failed', reasonCode: 'plan_not_allowed', retry: false }
          : { ok: true, ownerId: USER, state: 'signed_in', retry: false, email: EMAIL, plan: 'max' };
      }
      return { ok: true, ownerId: USER, state: 'cancelled' };
    };
    relay = new InMemorySignInRelay((_attempt, caller, request) => worker(request, caller));
    policies = new FakeModelAccessPolicies();
    access = testModelAccess({ policies });
    audited = auditedControlPlane();
    service = new EngineSignInService({ store, relay, access, transaction: audited.transaction, logger, now: () => 1_792_000_000_000 });
  });

  it('SA-1: allowed by default: each person turns their own on, nobody else', async () => {
    const view = await service.view(ORG, USER);
    expect(view.claude).to.deep.include({ state: 'available', enabled: false });
    expect(view.claude).to.not.have.property('reason');
    expect(view.chatgpt).to.deep.equal({ state: 'waiting_vendor' });
    expect((await service.view(ORG, OTHER)).claude.enabled).to.equal(false);
  });

  it('SA-1b: an org switch an extension binds, off, refuses turning it on and a code', async () => {
    const off = new EngineSignInService({
      store, relay, access, transaction: audited.transaction, orgSwitch: { allows: async () => false },
    });
    expect((await off.view(ORG, USER)).claude).to.deep.include({ state: 'not_allowed', reason: 'engine_signin_off' });
    expect(code(await refusal(off.setEnabled(ORG, USER, true)))).to.equal('engine_signin_off');
    expect(code(await refusal(off.submitCode({ orgId: ORG, userId: USER }, ATTEMPT, CODE)))).to.equal('engine_signin_off');
    expect(relay.asked).to.deep.equal([]);
  });

  it('SA-2: a person turns it on; a service account cannot', async () => {
    expect((await service.view(ORG, USER)).claude).to.deep.include({ state: 'available', enabled: false });
    expect((await service.setEnabled(ORG, USER, true)).claude.enabled).to.equal(true);
    expect((await service.view(ORG, 'svc-1')).claude.reason).to.equal('account_requires_interactive');
    expect(code(await refusal(service.setEnabled(ORG, 'svc-1', true)))).to.equal('account_requires_interactive');
  });

  it('SA-3: describe answers the owner only; anyone else, or an ended attempt, is not found', async () => {
    const view = await service.describe({ orgId: ORG, userId: USER }, ATTEMPT);
    expect(view).to.deep.include({ attemptId: ATTEMPT, runId: 'run-1', state: 'waiting', link: LINK });
    expect(await refusal(service.describe({ orgId: ORG, userId: OTHER }, ATTEMPT))).to.be.instanceOf(HarnessNotFoundError);
    worker = () => null;
    expect(await refusal(service.describe({ orgId: ORG, userId: USER }, ATTEMPT))).to.be.instanceOf(HarnessNotFoundError);
  });

  it('SA-4: an answer naming another owner is not found, whatever it says', async () => {
    worker = () => ({ ok: true, ownerId: OTHER, state: 'waiting', link: LINK, expiresAt: 1 });
    expect(await refusal(service.describe({ orgId: ORG, userId: USER }, ATTEMPT))).to.be.instanceOf(HarnessNotFoundError);
  });

  it('CLEANUP-S3: an answer that names no owner is not found either, for every request', async () => {
    await service.setEnabled(ORG, USER, true);
    worker = (request) =>
      request.op === 'describe'
        ? { ok: true, state: 'waiting', link: LINK, expiresAt: 1 }
        : request.op === 'code'
          ? { ok: true, state: 'signed_in', retry: false, email: EMAIL, plan: 'max' }
          : { ok: true, state: 'cancelled' };
    const caller = { orgId: ORG, userId: USER };
    expect(await refusal(service.describe(caller, ATTEMPT))).to.be.instanceOf(HarnessNotFoundError);
    expect(await refusal(service.submitCode(caller, ATTEMPT, CODE))).to.be.instanceOf(HarnessNotFoundError);
    expect(await refusal(service.cancel(caller, ATTEMPT))).to.be.instanceOf(HarnessNotFoundError);
    expect((await service.view(ORG, USER)).claude).to.not.have.property('email');
  });

  it('SA-5: the code goes to the worker with the org\'s plan rule; on success the email and plan are kept', async () => {
    await service.setEnabled(ORG, USER, true);
    await consumerPlans(true);
    const result = await service.submitCode({ orgId: ORG, userId: USER }, ATTEMPT, CODE);
    expect(result).to.deep.equal({ state: 'signed_in', retry: false, email: EMAIL, plan: 'max' });
    const sent = relay.asked.find((a) => a.request.op === 'code');
    expect(sent?.request).to.deep.equal({ op: 'code', code: CODE, workPlansOnly: false });
    expect((await service.view(ORG, USER)).claude).to.deep.include({
      state: 'connected', email: EMAIL, plan: 'max', workPlan: false, enabled: true,
    });
  });

  it('SA-6: with consumer plans off (the default) the worker is told work plans only, and nothing is kept', async () => {
    await service.setEnabled(ORG, USER, true);
    const result = await service.submitCode({ orgId: ORG, userId: USER }, ATTEMPT, CODE);
    expect(result).to.deep.equal({ state: 'failed', reasonCode: 'plan_not_allowed', retry: false });
    expect(relay.asked.find((a) => a.request.op === 'code')?.request).to.deep.include({ workPlansOnly: true });
    expect((await service.view(ORG, USER)).claude.email).to.equal(undefined);
  });

  it('SA-8: without a relay the sign-in API answers unavailable', async () => {
    const bare = new EngineSignInService({ store, access, transaction: audited.transaction });
    expect(await refusal(bare.describe({ orgId: ORG, userId: USER }, ATTEMPT))).to.be.instanceOf(HarnessUnavailableError);
  });

  it('DC-1: disconnect ends every sandbox of the person and keeps only when', async () => {
    await service.setEnabled(ORG, USER, true);
    await consumerPlans(true);
    await service.submitCode({ orgId: ORG, userId: USER }, ATTEMPT, CODE);
    const view = await service.disconnect(ORG, USER, USER, 'user');
    expect(view.claude).to.deep.include({ state: 'disconnected', enabled: false });
    expect(view.claude.email).to.equal(undefined);
    expect(view.claude.disconnectedAt).to.equal(new Date(1_792_000_000_000).toISOString());
    expect(relay.revoked).to.deep.include({ orgId: ORG, userId: USER, reason: 'user' });
  });

  it('DC-2: turning the switch off is a disconnect; an admin can sign a member out', async () => {
    await service.setEnabled(ORG, USER, true);
    await service.setEnabled(ORG, USER, false);
    expect(relay.revoked.at(-1)).to.deep.equal({ orgId: ORG, userId: USER, reason: 'user' });
    await service.setEnabled(ORG, USER, true);
    await service.disconnect(ORG, USER, 'admin-1', 'admin');
    expect(relay.revoked.at(-1)).to.deep.equal({ orgId: ORG, userId: USER, reason: 'admin' });
  });

  it('DC-3: an org rule a kept sign-in was checked against changed: every sign-in in the org ends', async () => {
    await service.orgRulesChanged(ORG);
    expect(relay.revoked).to.deep.equal([{ orgId: ORG, reason: 'policy' }]);
    expect(lines.at(-1)?.[0]).to.equal('engine_account.org_rules_changed');
  });

  it('DC-3: the caller names the change in the log, or logs its own line', async () => {
    await service.orgRulesChanged(ORG, 'engine_account.rule_x_off');
    expect(lines.at(-1)?.[0]).to.equal('engine_account.rule_x_off');
    const before = lines.length;
    await service.orgRulesChanged(ORG, null);
    expect(lines).to.have.length(before);
    expect(relay.revoked).to.have.length(2);
  });

  it('DC-4: a conversation shared ends the sign-in held in it', async () => {
    await service.sessionShared(ORG, 'ses-1');
    expect(relay.revoked).to.deep.equal([{ orgId: ORG, sessionId: 'ses-1', reason: 'shared' }]);
  });

  it('DC-5: erasure removes the records and ends live sign-ins; an unreachable relay does not block it', async () => {
    await service.setEnabled(ORG, USER, true);
    await service.eraseUser(ORG, USER);
    expect((await store.read(ORG, USER))?.stored).to.deep.equal({ enabled: false });
    expect(relay.revoked.at(-1)).to.deep.equal({ orgId: ORG, userId: USER, reason: 'erasure' });
    await service.eraseOrg(ORG);
    expect(await store.orgAllows(ORG)).to.equal(false);
    const down = engineSignInEraser(store, { revoke: () => Promise.reject(new Error('redis down')) });
    await down.eraseUser(ORG, OTHER);
    await down.eraseOrg(ORG);
  });

  it('DC-6: a revoke that fails is logged with ids only and does not fail the disconnect', async () => {
    relay.revoke = () => Promise.reject(new Error('redis down'));
    await service.disconnect(ORG, USER, USER, 'user');
    expect(lines.some(([event]) => event === 'engine_account.revoke_failed')).to.equal(true);
  });

  it('LK-N1..LK-N3: no code, link or email in the audit entries or the log lines', async () => {
    await service.setEnabled(ORG, USER, true);
    await consumerPlans(true);
    await service.describe({ orgId: ORG, userId: USER }, ATTEMPT);
    await service.submitCode({ orgId: ORG, userId: USER }, ATTEMPT, CODE);
    await service.disconnect(ORG, USER, USER, 'user');
    const audit = JSON.stringify(audited.configEntries(ORG));
    const logged = JSON.stringify(lines);
    for (const secret of [CODE, 'SECRETCODE123', LINK, EMAIL]) {
      expect(audit, `audit holds ${secret}`).not.to.include(secret);
      expect(logged, `log holds ${secret}`).not.to.include(secret);
    }
    expect(audited.configEntries(ORG).length).to.equal(3);
  });
});

describe('SIGNIN: model access pins the person\'s own plan (AD-18..AD-21)', () => {
  let usable: { enabled: boolean; allowed: boolean };
  let access: ModelAccessService;
  let asked: number;

  const subject = (kind: string, context = { via: 'session', surface: 'web', shared: false }) => ({
    orgId: ORG,
    actorId: USER,
    definition: null,
    model: { chatMode: 'auto' as const },
    engineProfile: profileOf(kind),
    signIn: () => {
      asked += 1;
      return Promise.resolve(context);
    },
  });

  beforeEach(() => {
    asked = 0;
    usable = { enabled: true, allowed: true };
    access = testModelAccess({ signIns: () => ({ usable: () => Promise.resolve(usable) }) });
  });

  it('AD-18: Claude Code on the person\'s plan: route vendor_sandbox_login, their own account, the profile narrowed to it', async () => {
    const decided = await access.decide(subject('claude_code'));
    expect(decided?.access.access).to.deep.equal({
      route: 'vendor_sandbox_login',
      accountKind: 'user_plan_sandbox_login',
      accountId: planAccountId(USER),
      pays: 'vendor_plan',
    });
    expect(decided?.access.route.provider).to.equal('anthropic');
    expect(decided?.engineProfile.accountKinds).to.deep.equal(['user_plan_sandbox_login']);
  });

  // ADR-MA-01 (R38-AUTHZ): a person's own plan runs a configured model, held to the org's groups.
  it('MA-PLAN-1: a turn naming no model runs the org\'s first configured Claude model, pinned with its key', async () => {
    const decided = await access.decide(subject('claude_code'));
    expect(decided?.access.route).to.deep.equal({ provider: 'anthropic', model: 'claude-x' });
    expect(decided?.access.model).to.deep.equal({ modelKey: 'model-2', modelName: 'claude-x' });
  });

  it('MA-PLAN-2: a turn naming a non-Claude model is refused, naming the Claude models to pick; an unknown one too', async () => {
    const named = (model: Record<string, string>) => ({ ...subject('claude_code'), model: { chatMode: 'auto' as const, ...model } });
    const refused = await refusal(access.decide(named({ modelKey: 'model-1', modelName: 'gpt-x' })));
    expect(code(refused)).to.equal('route_not_configured');
    expect(refused.message).to.contain('Claude models only').and.contain('claude-x');
    for (const model of [
      { modelKey: 'model-2', modelName: 'Claude-X' },
      { modelKey: 'model-2', modelName: 'claude-x ' },
      { modelKey: 'model-2', modelName: 'claude-opus-unconfigured' },
      { modelKey: 'model-9', modelName: 'claude-x' },
    ]) {
      expect(code(await refusal(access.decide(named(model)))), JSON.stringify(model)).to.equal('route_not_configured');
    }
    const ok = await access.decide(named({ modelKey: 'model-2', modelName: 'claude-x' }));
    expect(ok?.access.access.route).to.equal('vendor_sandbox_login');
  });

  it('MA-PLAN-3: an org with no Claude model configured refuses the plan and says what to do', async () => {
    const plain = testModelAccess({
      signIns: () => ({ usable: () => Promise.resolve(usable) }),
      models: new InMemoryConfiguredModels([DEFAULT_MODEL]),
    });
    const refused = await refusal(plain.decide(subject('claude_code')));
    expect(code(refused)).to.equal('route_not_configured');
    expect(refused.message).to.contain('none set up').and.contain('Use my Claude account');
  });

  it('MA-PLAN-4: the org\'s model groups hold the plan\'s model, and the refusal names what the person may use', async () => {
    const decided = await access.decide(subject('claude_code'));
    const groups = new FakeGroupAccess({
      policies: { read: async () => ({ models: { 'model-2': ['claude-team'] } }) },
      membership: { memberOfAny: async () => false },
      models: new InMemoryConfiguredModels(),
    });
    const step = new ModelGroupStep(groups);
    let refused: unknown;
    try {
      await step.run({
        request: { orgId: ORG, actorId: USER, input: { model: { chatMode: 'auto' } } },
        agentDefinition: null,
        modelAccess: decided?.access,
      } as never);
    } catch (error) {
      refused = error;
    }
    expect((refused as AdmissionRefusal).reason).to.equal('model_group_required');
    const message = ((refused as AdmissionRefusal).error as Error).message;
    expect(message).to.contain('Models you can use: gpt-5, gpt-5-mini, gpt-x.');
    expect(message).not.to.contain('claude-x');
  });

  it('AD-19: the consumer row is not refused at admission (the worker gates the plan it then sees)', async () => {
    const policy = await access.policy(ORG);
    expect(policy.consumerPlans).to.equal(false);
    expect((await access.decide(subject('claude_code')))?.access.access.route).to.equal('vendor_sandbox_login');
  });

  it('AD-20: never a silent fallback: a refused context or an org switched off refuses the turn', async () => {
    for (const [context, expected] of [
      [{ via: 'trigger', shared: false }, 'account_requires_interactive'],
      [{ via: 'service', shared: false }, 'account_requires_interactive'],
      [{ via: 'session', surface: 'slack', shared: false }, 'account_requires_interactive'],
      [{ via: 'session', surface: 'web', shared: true }, 'account_shared_session'],
    ] as const) {
      expect(code(await refusal(access.decide(subject('claude_code', context as never))))).to.equal(expected);
    }
    usable = { enabled: true, allowed: false };
    expect(code(await refusal(access.decide(subject('claude_code'))))).to.equal('engine_signin_off');
  });

  it('AD-21: off, or another engine: the plan is never read or used', async () => {
    const route = async (kind: string): Promise<string> => {
      try {
        return (await access.decide(subject(kind)))?.access.access.route ?? 'none';
      } catch (error) {
        return code(error as Error);
      }
    };
    usable = { enabled: false, allowed: true };
    expect(await route('claude_code')).not.to.equal('vendor_sandbox_login');
    usable = { enabled: true, allowed: true };
    expect(await route('codex')).not.to.equal('vendor_sandbox_login');
    expect(asked).to.equal(0);
  });
});

describeEngineSignInStoreContract('InMemoryEngineSignIns', async () => {
  const store = new InMemoryEngineSignIns();
  for (const user of ['u1', 'u2']) store.addUser('org-1', user);
  store.addUser('org-1', 'svc-1', true);
  store.addUser('org-2', 'u9');
  return { store, orgId: 'org-1', users: ['u1', 'u2'], serviceAccount: 'svc-1', stranger: { orgId: 'org-2', userId: 'u9' } };
});
