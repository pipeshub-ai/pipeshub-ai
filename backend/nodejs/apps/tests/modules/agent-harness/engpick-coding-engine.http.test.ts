/**
 * ENGPICK over HTTP: the coding-engine routes (the person's choice, the org's
 * default) and the agent save check, through the real routers. Every 2xx
 * answer is checked against the `CodingEngineView` contract.
 */
import { expect } from 'chai';
import { Container } from 'inversify';
import { InMemoryCodingEnginePreferences } from '../../../src/modules/agent-harness/adapters/in-memory-coding-engine-preferences';
import { CodingEngineService } from '../../../src/modules/agent-harness/application/coding-engine-service';
import { AGENT_HARNESS_CODING_ENGINES } from '../../../src/modules/agent-harness/composition';
import { agentEngineCheck } from '../../../src/modules/agent-harness/transport/model-account-routes';
import { HttpError } from '../../../src/libs/errors/http.errors';
import { InMemoryConfiguredModels, testModelAccess } from './support/model-access';
import { Harness, startHarness } from './support/model-accounts';
import { inRequestOrg } from '../../helpers/request-org-context';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const MINE = '/api/v1/agent-harness/model-accounts/coding-engine';
const ORG_DEFAULT = '/api/v1/agent-harness/admin/coding-engine';
const ADMIN = { userId: 'admin-1', orgId: 'org-1' };
const USER = { userId: 'u1', orgId: 'org-1' };
const ANTHROPIC_ONLY = [{ provider: 'anthropic', modelKey: 'm-a', modelNames: ['claude-x'], isDefault: true }];

describe('ENGPICK coding-engine routes over HTTP', () => {
  const settingsRefused = async () => {
    await settings.write('org-1', { engines: ['opencode'] }, 'p_admin');
    expect((await h.call('PUT', MINE, { choice: 'codex' })).status).to.equal(400);
  };
  let h: Harness;
  let settings: FakeEngineCeilings;
  let preferences: InMemoryCodingEnginePreferences;

  const bind = (container: Container, offering: 'oss' | 'managed' = 'oss') => {
    if (container.isBound(AGENT_HARNESS_CODING_ENGINES)) container.unbind(AGENT_HARNESS_CODING_ENGINES);
    container.bind(AGENT_HARNESS_CODING_ENGINES).toConstantValue(
      new CodingEngineService({
        ceilings: settings,
        access: testModelAccess({
          offering,
          ...(offering === 'managed' ? { models: new InMemoryConfiguredModels(ANTHROPIC_ONLY) } : {}),
        }),
        preferences,
        transaction: h.audit.transaction,
      }),
    );
  };

  beforeEach(async () => {
    h = await startHarness();
    preferences = new InMemoryCodingEnginePreferences();
    settings = new FakeEngineCeilings();
    bind(h.container);
  });
  afterEach(() => h.close());

  it('HT-1: a person reads the allowed engines and stores their own choice; nobody else’s changes', async () => {
    h.setCaller(USER);
    const read = await h.call('GET', MINE);
    expect(read.status, read.text).to.equal(200);
    expect(read.json).to.deep.include({ orgDefault: null, choice: 'org_default' });
    expect(read.json.engines.map((e: { kind: string }) => e.kind)).to.include.members(['opencode', 'codex', 'claude_code']);
    const saved = await h.call('PUT', MINE, { choice: 'codex' });
    expect(saved.status, saved.text).to.equal(200);
    expect(saved.json.choice).to.equal('codex');
    expect((await preferences.read('org-1', 'u1')).user).to.equal('codex');
    expect((await preferences.read('org-1', 'u2')).user).to.equal(null);
    expect((await h.call('PUT', MINE, { choice: 'none' })).json.choice).to.equal('none');
  });

  it('HT-6: every saved choice is one audited control-plane write naming the coding engine it changed', async () => {
    h.setCaller(USER);
    const before = h.audit.configEntries('org-1').length;
    await h.call('PUT', MINE, { choice: 'codex' });
    h.setCaller(ADMIN, true);
    await h.call('PUT', ORG_DEFAULT, { defaultEngine: 'opencode' });
    const objects = h.audit
      .configEntries('org-1')
      .slice(before)
      .map((entry) => [entry.fields.objectType, entry.fields.objectId]);
    expect(objects).to.deep.equal([
      ['coding-engine', 'user/u1'],
      ['coding-engine', 'org'],
    ]);
    h.setCaller(USER);
    await settingsRefused();
    expect(h.audit.configEntries('org-1').length, 'a refused choice is not audited').to.equal(before + 2);
  });

  it('HT-2: an engine the org ceiling leaves out is refused with the ceiling’s code; nothing is stored', async () => {
    await settings.write('org-1', { engines: ['opencode'] }, 'p_admin');
    h.setCaller(USER);
    const refused = await h.call('PUT', MINE, { choice: 'codex' });
    expect(refused.status).to.equal(400);
    expect(refused.json.error.code).to.equal('HTTP_AGENT_HARNESS_ENGINE_PROFILE_WIDENING');
    expect((await preferences.read('org-1', 'u1')).user).to.equal(null);
  });

  it('HT-3: Claude Code on the managed offering over the org’s Anthropic models is refused with the vendor rule’s code', async () => {
    bind(h.container, 'managed');
    h.setCaller(USER);
    const read = await h.call('GET', MINE);
    expect(read.json.engines.find((e: { kind: string }) => e.kind === 'claude_code')).to.deep.equal({
      kind: 'claude_code',
      available: false,
      reason: { code: 'OFFERING_NOT_PERMITTED' },
    });
    const refused = await h.call('PUT', MINE, { choice: 'claude_code' });
    expect(refused.status).to.equal(403);
    expect(refused.json.error.code).to.equal('HTTP_AGENT_HARNESS_OFFERING_NOT_PERMITTED');
  });

  it('HT-4: a connected app cannot set a person’s engine, and a body that is not a choice is refused', async () => {
    h.setCaller({ ...USER, isOAuth: true, oauthScopes: ['user:write'] });
    expect((await h.call('PUT', MINE, { choice: 'codex' })).status).to.equal(403);
    h.setCaller(USER);
    expect((await h.call('PUT', MINE, { choice: 'Codex!' })).status).to.equal(400);
    expect((await h.call('PUT', MINE, { choice: 'codex', userId: 'u2' })).status).to.equal(400);
    expect((await preferences.read('org-1', 'u1')).user).to.equal(null);
  });

  it('HT-5: only an org admin sets the org default; every member then reads it; null clears it', async () => {
    h.setCaller(USER, false);
    expect((await h.call('PUT', ORG_DEFAULT, { defaultEngine: 'codex' })).status).to.equal(403);
    h.setCaller(ADMIN, true);
    const saved = await h.call('PUT', ORG_DEFAULT, { defaultEngine: 'codex' });
    expect(saved.status, saved.text).to.equal(200);
    h.setCaller(USER, false);
    expect((await h.call('GET', MINE)).json.orgDefault).to.equal('codex');
    h.setCaller(ADMIN, true);
    expect((await h.call('PUT', ORG_DEFAULT, { defaultEngine: null })).json.orgDefault).to.equal(null);
    h.setCaller({ userId: 'u9', orgId: 'org-2' }, true);
    await h.call('PUT', ORG_DEFAULT, { defaultEngine: 'opencode' });
    expect((await preferences.read('org-1', 'u1')).org).to.equal(null);
  });

  describe('AS: the agent save check', () => {
    const run = async (container: Container, body: unknown): Promise<unknown> =>
      new Promise((resolve) => {
        void inRequestOrg(agentEngineCheck(container))(
          { body, user: { orgId: 'org-1', userId: 'u1' } } as never,
          {} as never,
          (error?: unknown) => resolve(error),
        );
      });

    it('AS-1: an agent naming an engine the org cannot use now is refused with that refusal; a usable one passes', async () => {
      bind(h.container, 'managed');
      const refused = await run(h.container, { engineProfile: { engines: ['claude_code'] } });
      expect(refused).to.be.instanceOf(HttpError);
      expect((refused as HttpError).statusCode).to.equal(403);
      expect(await run(h.container, { engineProfile: { engines: ['codex@1.0'] } })).to.equal(undefined);
    });

    it('AS-2: a body without engines, a loop agent, and a deployment without coding engines pass through unchecked', async () => {
      await settings.write('org-1', { engines: ['opencode'] }, 'p_admin');
      expect(await run(h.container, { name: 'A' })).to.equal(undefined);
      expect(await run(h.container, { engineProfile: null })).to.equal(undefined);
      expect(await run(h.container, { engineProfile: { mode: 'plan' } })).to.equal(undefined);
      expect(await run(new Container(), { engineProfile: { engines: ['codex'] } })).to.equal(undefined);
    });
  });
});
