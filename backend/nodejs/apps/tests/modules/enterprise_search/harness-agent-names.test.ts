/**
 * C11.07 (WP-51): the usage report and the budget notices name agents from
 * the query service's internal names route, with a service token scoped
 * exactly `agent:names` for the page's org; a failed answer is an error,
 * never a name.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import jwt from 'jsonwebtoken';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import {
  AGENT_NAMES_PER_REQUEST,
  HarnessAgentNames,
} from '../../../src/modules/enterprise_search/adapters/harness-agent-names';

const config = {
  aiBackend: 'http://query:8000',
  jwtSecret: 'user-secret',
  scopedJwtSecret: 'scoped-secret',
};

describe('WP-51 HarnessAgentNames', () => {
  afterEach(() => sinon.restore());

  const capture = (answer: (body: { agentKeys: string[] }) => { statusCode: number; data: unknown }) => {
    const calls: Array<{ uri: string; method: string; body: string; headers: Record<string, string> }> = [];
    sinon.stub(AIServiceCommand.prototype, 'execute').callsFake(function (this: AIServiceCommand<unknown>) {
      const call = this as unknown as { uri: string; method: string; body: string; headers: Record<string, string> };
      calls.push(call);
      return Promise.resolve(answer(JSON.parse(call.body)) as never);
    });
    return calls;
  };

  it('C11.07: names an org’s agents by key from an org-scoped agent:names POST', async () => {
    const calls = capture(() => ({ statusCode: 200, data: { names: { sales: 'Sales research', other: 'Not asked' } } }));
    const names = await new HarnessAgentNames(() => config).names('org-1', ['sales', 'gone', 'sales']);
    expect([...names]).to.deep.equal([['sales', 'Sales research']]);
    expect(calls).to.have.length(1);
    expect(calls[0]!.uri).to.equal('http://query:8000/api/v1/agent/internal/names');
    expect(calls[0]!.method).to.equal('POST');
    expect(JSON.parse(calls[0]!.body)).to.deep.equal({ agentKeys: ['sales', 'gone'] });
    const authorization = Object.entries(calls[0]!.headers).find(([name]) => name.toLowerCase() === 'authorization')![1];
    const claims = jwt.verify(authorization.replace('Bearer ', ''), 'scoped-secret') as Record<string, unknown>;
    expect(claims['orgId']).to.equal('org-1');
    expect(claims['scopes']).to.deep.equal(['agent:names']);
    expect(claims).to.not.have.property('userId');
  });

  it('C11.07: no keys asks nothing; many keys are asked in bounded batches', async () => {
    const calls = capture((body) => ({ statusCode: 200, data: { names: Object.fromEntries(body.agentKeys.map((k) => [k, `n-${k}`])) } }));
    expect((await new HarnessAgentNames(() => config).names('org-1', [])).size).to.equal(0);
    expect(calls).to.have.length(0);
    const keys = Array.from({ length: AGENT_NAMES_PER_REQUEST + 1 }, (_, i) => `a${String(i)}`);
    expect((await new HarnessAgentNames(() => config).names('org-1', keys)).size).to.equal(keys.length);
    expect(calls.map((c) => JSON.parse(c.body).agentKeys.length)).to.deep.equal([AGENT_NAMES_PER_REQUEST, 1]);
  });

  for (const answer of [
    { statusCode: 503, data: {} },
    { statusCode: 403, data: { detail: 'no' } },
    { statusCode: 200, data: { names: { sales: 7 } } },
    { statusCode: 200, data: null },
  ]) {
    it(`C11.07: ${String(answer.statusCode)} ${JSON.stringify(answer.data)} is an error, never a name`, async () => {
      capture(() => answer);
      let failed: unknown;
      await new HarnessAgentNames(() => config).names('org-1', ['sales']).catch((error: unknown) => {
        failed = error;
      });
      expect(failed).to.be.instanceOf(Error);
    });
  }

  it('ADR-UI-03: summaries ask names and latest versions in the same request, a version absent reads null', async () => {
    const calls = capture(() => ({
      statusCode: 200,
      data: { names: { sales: 'Sales research', draft: 'Draft only' }, latestVersions: { sales: 4 } },
    }));
    const found = await new HarnessAgentNames(() => config).summaries('org-1', ['sales', 'draft', 'gone']);
    expect([...found]).to.deep.equal([
      ['sales', { name: 'Sales research', latestVersion: 4 }],
      ['draft', { name: 'Draft only', latestVersion: null }],
    ]);
    expect(calls).to.have.length(1);
    expect(JSON.parse(calls[0]!.body)).to.deep.equal({
      agentKeys: ['sales', 'draft', 'gone'],
      include: ['latestVersion'],
    });
  });

  it('ADR-UI-03: a query service older than the versions answers names only; the versions read null', async () => {
    capture(() => ({ statusCode: 200, data: { names: { sales: 'Sales research' } } }));
    const found = await new HarnessAgentNames(() => config).summaries('org-1', ['sales']);
    expect(found.get('sales')).to.deep.equal({ name: 'Sales research', latestVersion: null });
  });

  it('ADR-UI-03: a malformed version is an error, never a version', async () => {
    capture(() => ({ statusCode: 200, data: { names: { sales: 'S' }, latestVersions: { sales: 'v2' } } }));
    let failed: unknown;
    await new HarnessAgentNames(() => config).summaries('org-1', ['sales']).catch((error: unknown) => {
      failed = error;
    });
    expect(failed).to.be.instanceOf(Error);
  });
});
