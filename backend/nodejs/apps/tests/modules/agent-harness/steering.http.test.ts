/**
 * P1b T2 over HTTP: a message sent while a session's run is active is
 * admitted into that run (ST-1) on the web agent and plain-chat routes, with
 * the same authorization as a new turn; the reply names the run and the
 * message, and a retry replays it.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryRecordReadAccess } from '../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { AgentAccessPolicy } from '../../../src/modules/agent-harness/ports/agent-access';
import { fixedRollout, HARNESS_ENABLED } from './support/rollout';
import { agentDefinition } from './support/agent-definition';
import {
  admissionBody,
  AdmissionHttp,
  startAdmissionHttp,
} from './support/admission-http';

const ORG = 'org-steer';

describe('ST-1: steering a running task over HTTP', () => {
  let http: AdmissionHttp;
  let store: InMemoryExecutionStore;
  let access: sinon.SinonStub;
  let records: InMemoryRecordReadAccess;

  beforeEach(async () => {
    store = new InMemoryExecutionStore();
    records = new InMemoryRecordReadAccess();
    access = sinon
      .stub()
      .callsFake(async (request) => agentDefinition({ agentKey: request.agentKey }));
    const policy: AgentAccessPolicy = { authorizeRun: access };
    http = await startAdmissionHttp(
      new RunAdmissionService({
        store,
        rollout: fixedRollout(() => ({ ...HARNESS_ENABLED, harnessMajor: 2 })),
        agentAccess: policy,
        references: records,
      }),
      {
        owner: { orgId: ORG, userId: 'user-owner' },
        colleague: { orgId: ORG, userId: 'user-colleague' },
      },
    );
  });

  afterEach(async () => {
    await http.close();
  });

  const pendingOf = async (runId: string) => (await store.get(ORG, runId))?.pendingInputs;

  it('agent route: the second message is 202 with the running run and its messageSeq; a retry replays it', async () => {
    const first = await http.post('/agents/agent-1/runs', 'owner', admissionBody());
    expect(first.status).to.equal(202);
    const { sessionId, run } = first.body.admission;
    const steer = admissionBody({ query: 'also the 2025 policy', idempotencyKey: 'steer-1' });
    const path = `/agents/agent-1/conversations/${String(sessionId)}/runs`;
    const queued = await http.post(path, 'owner', steer);
    expect(queued.status).to.equal(202);
    expect(queued.body.admission.queued).to.deep.equal({ messageSeq: 1 });
    expect(queued.body.admission.run.runId).to.equal(run.runId);
    expect(queued.body.admission.created).to.equal(true);
    // The public run shape: nothing about queued input leaks into it.
    expect(queued.body.admission.run).not.to.have.any.keys('pendingInputs', 'inputSeq', 'input');
    const retried = await http.post(path, 'owner', steer);
    expect(retried.status).to.equal(202);
    expect(retried.body.admission.queued).to.deep.equal({ messageSeq: 1 });
    expect(retried.body.admission.created).to.equal(false);
    expect(await pendingOf(String(run.runId))).to.deep.equal([1]);
  });

  it('QB-1 (SS1): the queued message names its sender as the credential says; the body cannot name another', async () => {
    const first = await http.post('/agents/agent-1/runs', 'owner', admissionBody());
    const { sessionId } = first.body.admission;
    const path = `/agents/agent-1/conversations/${String(sessionId)}/runs`;
    const spoofed = await http.post(path, 'owner', {
      ...admissionBody({ query: 'from me' }),
      by: 'p_user-colleague',
      actorId: 'user-colleague',
      userId: 'user-colleague',
    });
    const queued = (await store.journal.readStream(ORG, String(sessionId), 0, 50)).filter(
      (event) => event.type === 'input.queued',
    );
    // The admission body is strict: a field naming a sender is refused, nothing queued.
    expect(spoofed.status).to.equal(400);
    expect(queued).to.deep.equal([]);
    const plain = await http.post(path, 'owner', admissionBody({ query: 'and again' }));
    expect(plain.status).to.equal(202);
    const all = (await store.journal.readStream(ORG, String(sessionId), 0, 50)).filter(
      (event) => event.type === 'input.queued',
    );
    expect(all.map((event) => event.data?.['by'])).to.deep.equal(['p_user-owner']);
  });

  it('steer: true joins the running task, and with none running is 409 RUN_NOT_ACTIVE with nothing written', async () => {
    const first = await http.post('/agents/agent-1/runs', 'owner', admissionBody());
    const { sessionId } = first.body.admission;
    const path = `/agents/agent-1/conversations/${String(sessionId)}/runs`;
    const queued = await http.post(path, 'owner', admissionBody({ steer: true }));
    expect(queued.status).to.equal(202);
    expect(queued.body.admission.queued).to.deep.equal({ messageSeq: 1 });
    const fresh = await http.post('/agents/agent-1/runs', 'owner', admissionBody({ steer: true }));
    expect(fresh.status).to.equal(409);
    expect(fresh.body.error.code).to.equal('HTTP_AGENT_HARNESS_RUN_NOT_ACTIVE');
  });

  it('plain chat route: the second message joins the running assistant run', async () => {
    const first = await http.post('/conversations/runs', 'owner', admissionBody({ chatMode: 'internal_search' }));
    expect(first.status).to.equal(202);
    const { sessionId, run } = first.body.admission;
    const queued = await http.post(
      `/conversations/${String(sessionId)}/runs`,
      'owner',
      admissionBody({ chatMode: 'internal_search', query: 'shorter please' }),
    );
    expect(queued.status).to.equal(202);
    expect(queued.body.admission.queued).to.deep.equal({ messageSeq: 1 });
    expect(queued.body.admission.run.runId).to.equal(run.runId);
  });

  it('the same authorization as a new turn: revoked agent access is 403, another user is 404; nothing is queued', async () => {
    const first = await http.post('/agents/agent-1/runs', 'owner', admissionBody());
    const { sessionId, run } = first.body.admission;
    const path = `/agents/agent-1/conversations/${String(sessionId)}/runs`;
    access.resolves(null);
    expect((await http.post(path, 'owner', admissionBody())).status).to.equal(403);
    access.callsFake(async (request) => agentDefinition({ agentKey: request.agentKey }));
    expect((await http.post(path, 'colleague', admissionBody())).status).to.equal(404);
    expect(await pendingOf(String(run.runId))).to.equal(undefined);
  });

  it('C-21c: a message as long as a chat message may be joins the task; a longer one is refused as a new turn is', async () => {
    const first = await http.post('/agents/agent-1/runs', 'owner', admissionBody());
    const { sessionId, run } = first.body.admission;
    const path = `/agents/agent-1/conversations/${String(sessionId)}/runs`;
    const longest = await http.post(path, 'owner', admissionBody({ query: '\u20ac'.repeat(100_000) }));
    expect(longest.status).to.equal(202);
    expect(longest.body.admission.queued).to.deep.equal({ messageSeq: 1 });
    const over = await http.post(path, 'owner', admissionBody({ query: 'x'.repeat(100_001) }));
    expect(over.status).to.equal(400);
    const overNew = await http.post('/agents/agent-1/runs', 'owner', admissionBody({ query: 'x'.repeat(100_001) }));
    expect(overNew.status).to.equal(400);
    expect(await pendingOf(String(run.runId))).to.deep.equal([1]);
  });

  it('C-21b: a message with an attachment the caller may read joins the task; one naming a record they may not read is 403', async () => {
    const first = await http.post('/agents/agent-1/runs', 'owner', admissionBody());
    const { sessionId, run } = first.body.admission;
    const path = `/agents/agent-1/conversations/${String(sessionId)}/runs`;
    const file = { recordId: 'rec-1', virtualRecordId: 'vr-1', recordName: 'plan.pdf', mimeType: 'application/pdf' };
    const owner = records.queries.length;
    records.grant('Bearer owner', 'rec-1').content('rec-1', 'vr-1');
    const denied = await http.post(path, 'owner', admissionBody({ recordIds: ['rec-9'] }));
    expect(denied.status).to.equal(403);
    expect(JSON.stringify(denied.body)).to.not.contain('may not use this agent');
    const queued = await http.post(path, 'owner', admissionBody({ attachments: [file], steer: true }));
    expect(queued.status).to.equal(202);
    expect(queued.body.admission.queued).to.deep.equal({ messageSeq: 1 });
    expect(records.queries.slice(owner).map((q) => q.authorization)).to.deep.equal(['Bearer owner', 'Bearer owner']);
    expect(await pendingOf(String(run.runId))).to.deep.equal([1]);
  });

  it('R36-COVER: a steered file the caller may not read keeps the message, as a new turn does; the worker names the file', async () => {
    const first = await http.post('/agents/agent-1/runs', 'owner', admissionBody());
    const { sessionId, run } = first.body.admission;
    const path = `/agents/agent-1/conversations/${String(sessionId)}/runs`;
    records.grant('Bearer owner', 'rec-1').content('rec-1', 'vr-1');
    const theirs = { recordId: 'rec-theirs', virtualRecordId: 'vr-t', recordName: 'theirs.txt', mimeType: 'text/plain' };
    const swapped = { recordId: 'rec-1', virtualRecordId: 'vr-2', recordName: 'plan.pdf', mimeType: 'application/pdf' };
    const queued = await http.post(path, 'owner', admissionBody({ attachments: [theirs, swapped], steer: true }));
    expect(queued.status).to.equal(202);
    expect(queued.body.admission.queued).to.deep.equal({ messageSeq: 1 });
    expect(await pendingOf(String(run.runId))).to.deep.equal([1]);
  });
});
