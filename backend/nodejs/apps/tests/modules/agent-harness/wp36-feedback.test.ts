/**
 * WP-36 G-15: a rating names the run and agent version that answered (FB-1),
 * and the extension points around ratings and erasure do nothing unless one
 * is bound (FB-5, FB-6).
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { RunFeedbackService, RatedAnswer } from '../../../src/modules/agent-harness/application/run-feedback';
import { createErasureSteps } from '../../../src/modules/agent-harness/application/session-erasure-steps';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { ErasureJob, ERASURE_STEPS } from '../../../src/modules/agent-harness/domain/session-erasure';
import { noRatedAnswerSink } from '../../../src/modules/agent-harness/ports/rated-answers';
import { noDataEraserExtension } from '../../../src/modules/agent-harness/ports/data-eraser-extension';

const ORG = 'org-1';

const run = (runId: string, extra: Partial<HarnessRun> = {}): HarnessRun =>
  ({ runId, orgId: ORG, sessionId: 's-1', actorId: 'u-1', harnessMajor: 2, status: 'completed', ...extra }) as HarnessRun;

const RUNS: HarnessRun[] = [
  run('r-agent', { agentKey: 'agent-a', agentVersion: 4 }),
  run('r-chat'),
  run('r-elsewhere', { sessionId: 's-other' }),
];

const runs = { get: async (orgId: string, runId: string) => RUNS.find((r) => r.orgId === orgId && r.runId === runId) ?? null };

const rating = (): RatedAnswer => ({
  orgId: ORG,
  userId: 'u-1',
  sessionId: 's-1',
  messageId: 'm-1',
  link: null,
  body: { isHelpful: false },
  question: () => Promise.reject(new Error('the question is read only when asked for')),
  answer: 'an answer',
});

describe('WP-36 G-15 feedback linked to the run and agent version (FB)', () => {
  it('FB-1: a rating links to the run that answered and the agent version it ran; a run of another conversation links nothing', async () => {
    const service = new RunFeedbackService(runs);
    expect(await service.link(ORG, 's-1', 'r-agent')).to.deep.equal({ runId: 'r-agent', agentKey: 'agent-a', agentVersion: 4 });
    expect(await service.link(ORG, 's-1', 'r-chat')).to.deep.equal({ runId: 'r-chat', agentKey: null, agentVersion: null });
    expect(await service.link(ORG, 's-1', 'r-elsewhere')).to.equal(null);
    expect(await service.link('org-2', 's-1', 'r-agent')).to.equal(null);
    expect(await service.link(ORG, 's-1', undefined)).to.equal(null);
  });

  it('FB-5: a rating goes to the bound sink; unbound, the default keeps nothing and never reads the question', async () => {
    const told: RatedAnswer[] = [];
    await new RunFeedbackService(runs, { rated: async (r) => void told.push(r) }).record(rating());
    expect(told).to.have.length(1);
    expect(told[0]!.answer).to.equal('an answer');
    await new RunFeedbackService(runs).record(rating());
    await noRatedAnswerSink.rated(rating());
  });
});

describe('Erasure extension point (FB-6)', () => {
  const job = { orgId: ORG, sessionId: 's-1' } as ErasureJob;
  const stepOf = (extension: Parameters<typeof createErasureSteps>[0]['extension']) =>
    createErasureSteps({ extension } as Parameters<typeof createErasureSteps>[0]).find((s) => s.step === 'extension_stores')!;

  it('FB-6: a conversation erasure runs the bound extension; unbound, the step is done with nothing counted', async () => {
    expect(ERASURE_STEPS).to.include('extension_stores');
    expect(await stepOf(undefined).erase(job, {} as never)).to.deep.equal({ kind: 'done', counts: {} });
    expect(await stepOf(null).erase(job, {} as never)).to.deep.equal({ kind: 'done', counts: {} });
    const erased: string[] = [];
    const outcome = await stepOf({ eraseSession: async (orgId, sessionId) => (erased.push(`${orgId}/${sessionId}`), 2) }).erase(job, {} as never);
    expect(outcome).to.deep.equal({ kind: 'done', counts: { extensionStores: 2 } });
    expect(erased).to.deep.equal([`${ORG}/s-1`]);
  });

  it('FB-6: the default extension erases nothing', async () => {
    expect(await noDataEraserExtension.eraseUser(ORG, 'u-1')).to.equal(0);
    expect(await noDataEraserExtension.eraseOrg(ORG)).to.equal(0);
    expect(await noDataEraserExtension.eraseSession(ORG, 's-1')).to.equal(0);
  });
});
