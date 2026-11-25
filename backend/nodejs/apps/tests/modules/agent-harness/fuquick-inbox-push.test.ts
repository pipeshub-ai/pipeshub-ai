/**
 * FU-QUICK (ADR-INBOX-02 D2, D4): the two gaps the ADR left. A change to a run's asks is pushed to everyone whose
 * approvals queue lists the run, not only its actor; and a queued message that drains after its chat ended on a
 * question pushes that question's asker, as an admitted turn does.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { InMemoryInboxStore } from '../../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import type { ApprovalQueueReach } from '../../../src/modules/agent-harness/application/approval-queue';
import { NextRunService } from '../../../src/modules/agent-harness/application/next-run-service';
import type { HarnessBindingContext } from '../../../src/modules/agent-harness/binders/context';
import { bindInbox, questionGoneAnnouncer } from '../../../src/modules/agent-harness/binders/inbox';
import {
  AGENT_HARNESS_APPROVAL_QUEUE_REACH,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_INBOX,
} from '../../../src/modules/agent-harness/binders/tokens';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { InboxNotices } from '../../../src/modules/agent-harness/ports/inbox';

const ORG = 'org-1';
const notices = { unreadCount: async () => 0 } as unknown as InboxNotices;
const approversReach: ApprovalQueueReach = {
  othersRuns: async (_viewer, standing) => standing.isApprover || standing.isOrgAdmin,
  audience: async () => ['bo'],
};

const run = { orgId: ORG, actorId: 'ana', runId: 'r-1', status: 'waiting_approval' } as HarnessRun;

async function until(done: () => boolean): Promise<void> {
  for (let i = 0; i < 200 && !done(); i += 1) await new Promise((resolve) => setImmediate(resolve));
  expect(done()).to.equal(true);
}

describe('FU-QUICK: Needs you is pushed to approvers and when a queued message moves a chat on (ADR-INBOX-02)', () => {
  it('FQI-1: with the default reach only the run’s actor is told', async () => {
    const container = new Container();
    const asksChanged: Array<(run: HarnessRun) => void> = [];
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({
      onRunEnded: () => undefined,
      onRunAsksChanged: (listener: (run: HarnessRun) => void) => asksChanged.push(listener),
      onRunWorkingChanged: () => undefined,
    });
    const pushed: string[] = [];
    bindInbox({
      container,
      logger: undefined,
      dependencies: {
        inbox: {
          store: new InMemoryInboxStore(),
          notices,
          publisher: { publish: (userId: string) => pushed.push(userId) },
        },
      },
    } as unknown as HarnessBindingContext);
    asksChanged[0]!(run);
    await until(() => pushed.length === 1);
    for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setImmediate(resolve));
    expect(pushed).to.deep.equal(['ana']);
  });

  it('FQI-3: bindInbox pushes the counts of the reach’s audience beside the actor’s once an edition binds a reach', async () => {
    const container = new Container();
    const asksChanged: Array<(run: HarnessRun) => void> = [];
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({
      onRunEnded: () => undefined,
      onRunAsksChanged: (listener: (run: HarnessRun) => void) => asksChanged.push(listener),
      onRunWorkingChanged: () => undefined,
    });
    const pushed: string[] = [];
    bindInbox({
      container,
      logger: undefined,
      dependencies: {
        inbox: {
          store: new InMemoryInboxStore(),
          notices,
          publisher: { publish: (userId: string) => pushed.push(userId) },
        },
      },
    } as unknown as HarnessBindingContext);
    container.bind(AGENT_HARNESS_APPROVAL_QUEUE_REACH).toConstantValue(approversReach);
    asksChanged[0]!(run);
    await until(() => pushed.length === 2);
    expect(pushed.sort()).to.deep.equal(['ana', 'bo']);
  });

  it('FQI-4: a queued message admitted after its chat ended on a question tells whose question it was; a chat on no question tells nobody', async () => {
    const told: string[] = [];
    const store = {
      nextPending: sinon.stub(),
      admit: sinon.stub().callsFake(async (command) => ({ command, created: true, run: { runId: command.runId } })),
      settleNextRun: sinon.stub().resolves(),
    };
    // Readmission decides what the drained message answers (FUQUICK-FU-1); the drain tells that run's asker.
    let answers: string | null = 'asked-1';
    const queued = (id: string) => ({
      id,
      orgId: ORG,
      sessionId: 's-1',
      actorId: 'bo',
      runId: `run-${id}`,
      behindRunId: 'asked-1',
      createdAt: 1,
      command: { orgId: ORG, actorId: 'bo', sessionId: 's-1', input: {} },
    });
    store.nextPending.onCall(0).resolves(queued('n-1'));
    const service = new NextRunService({
      store: store as never,
      readmission: {
        readmit: async (command: Record<string, unknown>) => ({
          command: answers === null ? command : { ...command, answered: { runId: answers } },
        }),
      } as never,
      clock: { now: () => 1_000 },
      questionAnswered: (orgId, askingRunId) => told.push(`${orgId}:${askingRunId}`),
    });
    expect(await service.drain(ORG, 's-1')).to.equal('admitted');
    expect(told).to.deep.equal([`${ORG}:asked-1`]);
    answers = null;
    store.nextPending.onCall(1).resolves(queued('n-2'));
    await service.drain(ORG, 's-1');
    expect(told).to.have.length(1);
    answers = 'asked-1';
    store.admit.callsFake(async (command) => ({ command, created: false, run: { runId: command.runId } }));
    store.nextPending.onCall(2).resolves(queued('n-3'));
    await service.drain(ORG, 's-1');
    expect(told, 'a replayed admission tells nobody again').to.have.length(1);
  });

  it('FQI-5: the question’s asker is pushed, read from the asking run, not the sender', async () => {
    const container = new Container();
    const announce = sinon.stub().resolves();
    container.bind(AGENT_HARNESS_INBOX).toConstantValue({ announce });
    container.bind(AGENT_HARNESS_EXECUTION_STORE).toConstantValue({
      get: async (_org: string, runId: string) => (runId === 'asked-1' ? { runId, actorId: 'ana' } : null),
    });
    const gone = questionGoneAnnouncer(container);
    gone(ORG, 'asked-1');
    gone(ORG, 'missing');
    await until(() => announce.callCount === 1);
    await new Promise((resolve) => setImmediate(resolve));
    expect(announce.args).to.deep.equal([[ORG, 'ana']]);
    expect(() => questionGoneAnnouncer(new Container())(ORG, 'asked-1')).not.to.throw();
  });
});
