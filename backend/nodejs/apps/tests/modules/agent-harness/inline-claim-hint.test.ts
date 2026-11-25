import { expect } from 'chai';
import sinon from 'sinon';
import { HttpInlineClaimHint } from '../../../src/modules/agent-harness/adapters/http/inline-claim-hint';
import { registerUpstreams } from '../../../src/libs/http/upstream-call';

describe('HttpInlineClaimHint (D-4)', () => {
  beforeEach(() => registerUpstreams({ query: ['http://query:8000'] }));
  afterEach(() => registerUpstreams({}));
  let fetchStub: sinon.SinonStub;
  const warnings: Array<[string, Record<string, unknown> | undefined]> = [];
  const logger = {
    warn: (message: string, metadata?: Record<string, unknown>) => {
      warnings.push([message, metadata]);
    },
  };

  beforeEach(() => {
    warnings.length = 0;
    fetchStub = sinon.stub(globalThis, 'fetch');
  });

  afterEach(() => {
    sinon.restore();
  });

  it('posts ids only, with the worker credential as bearer', async () => {
    fetchStub.resolves(
      new Response(JSON.stringify({ accepted: true }), {
        status: 202,
        headers: { 'content-type': 'application/json' },
      }),
    );
    await new HttpInlineClaimHint(
      () => 'http://query:8000',
      async () => 'phw_secret',
      logger,
    ).hint({ orgId: 'org-1', runId: 'run/1' });
    const [url, init] = fetchStub.firstCall.args as [string, RequestInit];
    expect(url).to.equal('http://query:8000/api/v1/harness/runs/run%2F1/claim');
    expect((init.headers as Record<string, string>)['authorization']).to.equal(
      'Bearer phw_secret',
    );
    expect(JSON.parse(init.body as string)).to.deep.equal({ orgId: 'org-1' });
    expect(warnings).to.deep.equal([]);
  });

  it('never rejects: a refused, failed or unprovisioned hint is only logged', async () => {
    fetchStub.resolves(new Response('{}', { status: 503 }));
    const hint = new HttpInlineClaimHint(
      () => 'http://query:8000',
      async () => 'phw_secret',
      logger,
    );
    await hint.hint({ orgId: 'org-1', runId: 'run-1' });
    fetchStub.rejects(new Error('connection refused'));
    await hint.hint({ orgId: 'org-1', runId: 'run-2' });
    await new HttpInlineClaimHint(
      () => 'http://query:8000',
      async () => null,
      logger,
    ).hint({ orgId: 'org-1', runId: 'run-3' });
    expect(warnings.map(([message]) => message)).to.deep.equal([
      'Inline claim hint refused',
      'Inline claim hint failed',
      'Inline claim hint skipped: no worker credential',
    ]);
  });
});
