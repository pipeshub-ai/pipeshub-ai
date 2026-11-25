/**
 * The model-account routes serve what a person may reach models with and
 * their coding engine; nobody stores a model key of their own here. Every run
 * reaches the models the admin configured in AI Models, shared with everyone.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Harness, KEYS, startHarness } from './support/model-accounts';

const MODEL_ACCOUNTS = '/api/v1/agent-harness/model-accounts';

describe('model-account routes: no personal model keys', () => {
  let h: Harness;

  beforeEach(async () => {
    h = await startHarness();
    h.setCaller({ userId: 'u1', orgId: 'org-1' });
  });

  afterEach(() => h.close());

  it('NR-0: no route lists, stores or removes a key of the caller\'s own; nothing reaches the vault', async () => {
    for (const [method, path, body] of [
      ['GET', `${MODEL_ACCOUNTS}/me`, undefined],
      ['PUT', `${MODEL_ACCOUNTS}/me/openAI`, { apiKey: KEYS.openAI }],
      ['DELETE', `${MODEL_ACCOUNTS}/me/openAI`, undefined],
    ] as const) {
      const answer = await fetch(`${h.base}${path}`, {
        method,
        headers: { 'content-type': 'application/json' },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
      });
      expect(answer.status, `${method} ${path}`).to.equal(404);
    }
    expect(await h.kv.raw.getAllKeys()).to.deep.equal([]);
  });

  it("NR-0: what the caller may use shows personal-key rows as not built, the org's rows as built", async () => {
    const access = await h.call('GET', `${MODEL_ACCOUNTS}/access`);
    expect(access.status).to.equal(200);
    const rules = (access.json as { rules: Array<{ accountKind: string; route: string; built: boolean }> }).rules;
    const gateway = rules.filter((rule) => rule.route === 'gateway');
    expect(gateway.filter((rule) => rule.accountKind === 'user_api_key').map((rule) => rule.built)).to.not.include(true);
    expect(gateway.filter((rule) => rule.accountKind === 'org_api_key').every((rule) => rule.built)).to.equal(true);
  });
});
