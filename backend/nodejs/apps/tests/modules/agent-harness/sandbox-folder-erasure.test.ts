/**
 * SF-1, SF-2 (E1, from the stage-1 E2E): an engine session's
 * folders on the broker's data volume are erased with the session, through
 * the broker's authenticated `/v1/erase-session`; a broker that cannot be
 * asked fails the step (it is retried), never skips it.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { HttpSandboxReaper } from '../../../src/modules/agent-harness/adapters/http/http-sandbox-reaper';
import { createErasureSteps } from '../../../src/modules/agent-harness/application/session-erasure-steps';
import { ErasureJob } from '../../../src/modules/agent-harness/domain/session-erasure';

const job: ErasureJob = {
  orgId: 'org-1',
  sessionId: 'ses-1',
  reason: 'user_delete',
  by: 'p_owner',
  status: 'pending',
  step: 'sandbox_folders',
  counts: {},
  attempt: 1,
  failures: 0,
  requestedAt: 0,
  nextAttemptAt: 0,
  completedAt: null,
  lastErrorType: null,
};

const stepWith = (sandboxFolders: unknown) => {
  const stub = {} as never;
  return createErasureSteps({
    runs: stub,
    executions: stub,
    workspaceKeys: stub,
    liveBus: null,
    sandboxFolders: sandboxFolders as never,
    artifacts: stub,
    stores: stub,
    conversations: stub,
    journal: stub,
  }).find((eraser) => eraser.step === 'sandbox_folders')!;
};

const noContext = { completeIn: () => Promise.resolve(true) };

describe('E1: sandbox folders are erased with their session', () => {
  it('SF-1: the step asks the broker for the job’s org and session and counts what went', async () => {
    const eraseSession = sinon.stub().resolves(1);
    const outcome = await stepWith({ eraseSession }).erase(job, noContext);
    expect(outcome).to.deep.equal({ kind: 'done', counts: { sandboxFolders: 1 } });
    expect(eraseSession.firstCall.args).to.deep.equal(['org-1', 'ses-1']);
  });

  it('SF-1: no broker in the deployment: nothing to erase', async () => {
    expect(await stepWith(null).erase(job, noContext)).to.deep.equal({ kind: 'done', counts: {} });
  });

  it('SF-1: a broker that cannot be asked fails the step, so the runner retries it', async () => {
    const eraseSession = sinon.stub().rejects(new Error('sandbox broker refused the erasure: HTTP 503'));
    let failed = false;
    try {
      await stepWith({ eraseSession }).erase(job, noContext);
    } catch {
      failed = true;
    }
    expect(failed).to.equal(true);
  });

  it('SF-2: the HTTP eraser posts org and session with the worker credential', async () => {
    const fetchImpl = sinon.stub().resolves(new Response(JSON.stringify({ erased: 1 }), { status: 200 }));
    const eraser = new HttpSandboxReaper(
      async () => 'http://sandbox-broker:8095/',
      async () => 'worker-cred',
      fetchImpl as unknown as typeof fetch,
    );
    expect(await eraser.eraseSession('org-1', 'ses-1')).to.equal(1);
    const [url, init] = fetchImpl.firstCall.args as [string, RequestInit];
    expect(url).to.equal('http://sandbox-broker:8095/v1/erase-session');
    expect((init.headers as Record<string, string>)['Authorization']).to.equal('Bearer worker-cred');
    expect(JSON.parse(init.body as string)).to.deep.equal({ orgId: 'org-1', sessionId: 'ses-1' });
  });

  it('SF-2: no broker configured erases nothing; a refusal or a malformed answer rejects', async () => {
    const none = new HttpSandboxReaper(async () => null, async () => 'worker-cred', sinon.stub() as never);
    expect(await none.eraseSession('org-1', 'ses-1')).to.equal(0);
    for (const response of [
      new Response('{}', { status: 401 }),
      new Response(JSON.stringify({ erased: 'x' }), { status: 200 }),
    ]) {
      const eraser = new HttpSandboxReaper(
        async () => 'http://sandbox-broker:8095',
        async () => 'worker-cred',
        sinon.stub().resolves(response) as unknown as typeof fetch,
      );
      let rejected = false;
      try {
        await eraser.eraseSession('org-1', 'ses-1');
      } catch {
        rejected = true;
      }
      expect(rejected).to.equal(true);
    }
  });
});
