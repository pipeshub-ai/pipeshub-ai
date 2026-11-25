/**
 * `HttpSandboxReaper` against a recorded fetch: the broker's reap, session and org erasures are posted with
 * the worker credential read on each call; a broker that refuses, or answers with a body that does not read,
 * fails the call (the caller retries); no broker configured means nothing of the org is on one.
 */
import { expect } from 'chai';
import { HttpSandboxReaper } from '../../../src/modules/agent-harness/adapters/http/http-sandbox-reaper';

interface Call {
  url: string;
  init: RequestInit;
}

function broker(answer: { status?: number; body?: unknown }) {
  const calls: Call[] = [];
  const fetchImpl = (async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(answer.body ?? {}), { status: answer.status ?? 200 });
  }) as unknown as typeof fetch;
  return { calls, fetchImpl };
}

const reaper = (
  fetchImpl: typeof fetch,
  endpoint: string | null = 'http://broker:8095//',
  credential: string | null = 'cred-1',
) => new HttpSandboxReaper(async () => endpoint, async () => credential, fetchImpl);

async function rejection(work: Promise<unknown>): Promise<Error> {
  try {
    await work;
  } catch (error) {
    return error as Error;
  }
  throw new Error('expected a rejection');
}

describe('HttpSandboxReaper: the broker’s org erasure', () => {
  it('posts the org to /v1/erase-org with the worker credential and returns what went', async () => {
    const b = broker({ body: { sandboxes: 2, folders: 3 } });
    expect(await reaper(b.fetchImpl).eraseOrg('org-1')).to.deep.equal({ sandboxes: 2, folders: 3 });
    expect(b.calls).to.have.length(1);
    expect(b.calls[0]?.url).to.equal('http://broker:8095/v1/erase-org');
    expect(b.calls[0]?.init.method).to.equal('POST');
    expect((b.calls[0]?.init.headers as Record<string, string>)['Authorization']).to.equal('Bearer cred-1');
    expect(JSON.parse(b.calls[0]?.init.body as string)).to.deep.equal({ orgId: 'org-1' });
  });

  it('no broker configured: nothing of the org is on one, and nothing is asked', async () => {
    const b = broker({ body: { sandboxes: 9, folders: 9 } });
    expect(await reaper(b.fetchImpl, null).eraseOrg('org-1')).to.deep.equal({ sandboxes: 0, folders: 0 });
    expect(b.calls).to.deep.equal([]);
  });

  it('a broker without the worker credential fails the step instead of skipping it', async () => {
    const b = broker({ body: { sandboxes: 0, folders: 0 } });
    const error = await rejection(reaper(b.fetchImpl, 'http://broker:8095', null).eraseOrg('org-1'));
    expect(error.message).to.equal('worker credential not provisioned');
    expect(b.calls).to.deep.equal([]);
  });

  it('a refusal names its HTTP status', async () => {
    const b = broker({ status: 503 });
    const error = await rejection(reaper(b.fetchImpl).eraseOrg('org-1'));
    expect(error.message).to.equal('sandbox broker refused the org erasure: HTTP 503');
  });

  for (const body of [{ sandboxes: 1 }, { folders: 1 }, { sandboxes: '1', folders: 1 }]) {
    it(`a body that does not read is malformed: ${JSON.stringify(body)}`, async () => {
      const b = broker({ body });
      const error = await rejection(reaper(b.fetchImpl).eraseOrg('org-1'));
      expect(error.message).to.equal('malformed erase-org response');
    });
  }
});

describe('HttpSandboxReaper: reap and session erasure refusals', () => {
  it('a reap the broker answers without a count is malformed', async () => {
    const b = broker({ body: { reaped: 'two' } });
    const error = await rejection(reaper(b.fetchImpl).reap({ run: 'r1' }));
    expect(error.message).to.equal('malformed reap response');
  });

  it('a reap without a broker or credential is refused before any request', async () => {
    const b = broker({ body: { reaped: 1 } });
    for (const [endpoint, credential] of [[null, 'c'], ['http://broker', null]] as const) {
      const error = await rejection(reaper(b.fetchImpl, endpoint, credential).reap({ run: 'r1' }));
      expect(error.message).to.equal('sandbox broker not configured');
    }
    expect(b.calls).to.deep.equal([]);
  });

  it('a session erasure without the worker credential fails the step', async () => {
    const b = broker({ body: { erased: 1 } });
    const error = await rejection(reaper(b.fetchImpl, 'http://broker', null).eraseSession('org-1', 'ses-1'));
    expect(error.message).to.equal('worker credential not provisioned');
    expect(b.calls).to.deep.equal([]);
  });

  it('a session erasure answered without a count is malformed', async () => {
    const b = broker({ body: { erased: null } });
    const error = await rejection(reaper(b.fetchImpl).eraseSession('org-1', 'ses-1'));
    expect(error.message).to.equal('malformed erase response');
  });
});
