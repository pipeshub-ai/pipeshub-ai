import { expect } from 'chai';
import { API_CONTRACTS, assertAnswersContract } from './support/api-contracts';
import { startHarness, type Harness } from './support/model-accounts';

const ACCESS = '/api/v1/agent-harness/model-accounts/access';

/**
 * CPD0-27: the approvals and AI-access routes answer the bodies the frontend's
 * generated types describe. The approvals, queue and engine-profile HTTP tests
 * check their answers through the same helper; this file covers the model
 * account and access routes end to end and the helper itself.
 */
describe('agent-harness API bodies are the generated contracts (CPD-0)', () => {
  let h: Harness;

  beforeEach(async () => {
    h = await startHarness();
  });
  afterEach(() => h.close());

  it('the access route answers within its contract', async () => {
    h.setCaller({ userId: 'u1', orgId: 'org-1' }, false);
    expect((await h.call('GET', ACCESS)).status).to.equal(200);
  });

  it('every route the pages read has a contract', () => {
    const routes = API_CONTRACTS.map((r) => `${r.method} ${r.path.source}`);
    expect(new Set(routes).size).to.equal(routes.length);
    expect(routes).to.have.length(13);
  });

  it('an answer outside its contract is refused, naming the route', () => {
    const answer = { rules: [{ id: 'openai', apiKey: 'sk-secret' }] };
    expect(() => assertAnswersContract('GET', ACCESS, 200, answer)).to.throw(/model-accounts\/access answered outside its contract/);
    expect(() => assertAnswersContract('GET', ACCESS, 403, answer)).to.not.throw();
  });
});
