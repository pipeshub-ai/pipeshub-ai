/**
 * CJ14-FU-1 (classic jobs §4.3 "Jobs 2", §6): the sidebar's Jobs entry counts the person's jobs at work with no
 * added request. The count rides on the Inbox counts the page already reads and is pushed; it is the Jobs page's
 * Working section, held to `contracts/harness/vectors/working-jobs.json` here and in the frontend.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Container } from 'inversify';
import { InMemoryInboxStore } from '../../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import { InboxFeedService } from '../../../src/modules/agent-harness/application/inbox-feed';
import type { HarnessBindingContext } from '../../../src/modules/agent-harness/binders/context';
import { bindInbox } from '../../../src/modules/agent-harness/binders/inbox';
import {
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_INBOX,
} from '../../../src/modules/agent-harness/binders/tokens';
import type { HarnessRun, RunStatus } from '../../../src/modules/agent-harness/domain/execution';
import { isWorkingJob, runListState } from '../../../src/modules/agent-harness/domain/run-lists';
import type { InboxCounts } from '../../../src/modules/agent-harness/domain/inbox';
import type { InboxNotices } from '../../../src/modules/agent-harness/ports/inbox';
import { workerEvent } from './support/journal-fixtures';
import { scheduledWorld, ScheduledWorld } from './support/scheduled-admission';
import { aSchedule, ALICE, ORG } from './support/schedules';

interface WorkingCase {
  name: string;
  status: RunStatus;
  openInteractions: string[];
  job: boolean;
  state: string;
  working: boolean;
}
const VECTOR = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/working-jobs.json'), 'utf8'),
) as { cases: WorkingCase[] };
const notices = { unreadCount: async () => 0, page: async () => ({ items: [] }) } as unknown as InboxNotices;
const LIMITS = { perActor: 50, perOrg: 500 };

const runOf = (c: Pick<WorkingCase, 'status' | 'openInteractions' | 'job'>) =>
  ({
    status: c.status,
    openInteractions: c.openInteractions,
    ...(c.job ? { parent: { runId: 'parent-1' } } : {}),
  }) as unknown as HarnessRun;

/** A fired schedule's run made a child of another, so the store treats it as a job (no spawning machinery here). */
async function queuedJob(w: ScheduledWorld): Promise<HarnessRun> {
  const schedule = await w.schedules.create(aSchedule({ agentKey: 'agent-1', deadlineS: 3600, name: 'Digest' }, w.clock.value), LIMITS);
  w.clock.value = (schedule.nextFireAt as number) + 1_000;
  await w.firer.tick();
  const [firing] = await w.schedules.firings(ORG, schedule.id, 1);
  const runs = (w.executions as unknown as { runs: Map<string, HarnessRun> }).runs;
  const run = [...runs.values()].find((r) => r.runId === firing!.runId)!;
  run.parent = { runId: 'parent-1' } as HarnessRun['parent'];
  return run;
}

async function until(done: () => boolean): Promise<void> {
  for (let i = 0; i < 200 && !done(); i += 1) await new Promise((resolve) => setImmediate(resolve));
  expect(done()).to.equal(true);
}

describe('CJ14-FU-1: the Jobs entry counts the jobs at work, on the counts the page already reads', () => {
  it('CJW-1: the shared table: the list state and the Working rule, case by case', () => {
    expect(VECTOR.cases.length).to.be.greaterThan(10);
    for (const c of VECTOR.cases) {
      expect(runListState(runOf(c)), c.name).to.equal(c.state);
      expect(isWorkingJob(runOf(c)), c.name).to.equal(c.working);
    }
  });

  it('CJW-2: the counts carry `working` when the server counts it, and none when it cannot', async () => {
    const feed = (workingJobs?: () => Promise<number>) =>
      new InboxFeedService({
        notices,
        waiting: [],
        preferences: new InMemoryInboxStore(),
        schedules: async () => false,
        ...(workingJobs !== undefined ? { workingJobs } : {}),
      });
    const viewer = { orgId: ORG, userId: ALICE, isAdmin: false };
    expect((await feed(async () => 2).counts(viewer)).working).to.equal(2);
    expect(await feed().counts(viewer)).not.to.have.property('working');
    expect(await feed(() => Promise.reject(new Error('down'))).counts(viewer)).not.to.have.property('working');
    const page = await feed(async () => 3).feed(viewer, { view: 'inbox', filter: 'all', limit: 30 });
    expect(page.counts.working).to.equal(3);
  });

  it('CJW-3: a job is told when its claim starts it, a release puts it back and an ask takes it out; a replay and a root run are not', async () => {
    const w = scheduledWorld();
    const told: string[] = [];
    w.service.onRunWorkingChanged((run) => told.push(run.status));
    const job = await queuedJob(w);
    const claim = (attemptId: string) =>
      w.service.claim({ orgId: ORG, runId: job.runId, workerId: 'worker-1', attemptId, leaseDurationMs: 60_000 });
    const first = await claim('attempt-1');
    await w.service.release({ orgId: ORG, runId: job.runId, workerId: 'worker-1', attemptId: 'attempt-1', fence: first.run.fence, reason: 'shutdown' });
    const second = await claim('attempt-2');
    expect(told).to.deep.equal(['running', 'queued', 'running']);
    const ask = {
      commandId: 'worker-1:ask',
      orgId: ORG,
      runId: job.runId,
      workerId: 'worker-1',
      attemptId: 'attempt-2',
      fence: second.run.fence,
      events: [workerEvent('interaction.requested', { interactionId: 'ix-1', kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` })],
      legacyEvents: [],
      outbox: [],
    };
    await w.service.commit(ask);
    await w.service.commit(ask);
    expect(told, 'the ask moved it to Needs you once; the replay was not told').to.deep.equal(['running', 'queued', 'running', 'running']);

    const root = scheduledWorld();
    const rootTold: string[] = [];
    root.service.onRunWorkingChanged((run) => rootTold.push(run.status));
    const schedule = await root.schedules.create(aSchedule({ agentKey: 'agent-1', deadlineS: 3600, name: 'Root' }, root.clock.value), LIMITS);
    root.clock.value = (schedule.nextFireAt as number) + 1_000;
    await root.firer.tick();
    const [firing] = await root.schedules.firings(ORG, schedule.id, 1);
    await root.service.claim({ orgId: ORG, runId: firing!.runId!, workerId: 'w', attemptId: 'a', leaseDurationMs: 60_000 });
    expect(rootTold, 'a chat’s own answer is not a job').to.deep.equal([]);
  });

  it('CJW-4: bindInbox counts the person’s active runs by the rule and pushes the actor when a job moves', async () => {
    const container = new Container();
    const workingChanged: Array<(run: HarnessRun) => void> = [];
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({
      onRunEnded: () => undefined,
      onRunAsksChanged: () => undefined,
      onRunWorkingChanged: (listener: (run: HarnessRun) => void) => workingChanged.push(listener),
    });
    container.bind(AGENT_HARNESS_EXECUTION_STORE).toConstantValue({
      activeRunsOfActor: async () => VECTOR.cases.filter((c) => !['completed', 'failed', 'cancelled'].includes(c.status)).map(runOf),
    });
    const pushed: Array<[string, number | undefined]> = [];
    bindInbox({
      container,
      logger: undefined,
      dependencies: {
        inbox: {
          store: new InMemoryInboxStore(),
          notices,
          publisher: { publish: (userId: string, counts: InboxCounts) => pushed.push([userId, counts.working]) },
        },
      },
    } as unknown as HarnessBindingContext);
    const feed = container.get<InboxFeedService>(AGENT_HARNESS_INBOX);
    const expected = VECTOR.cases.filter((c) => c.working).length;
    expect((await feed.counts({ orgId: ORG, userId: ALICE, isAdmin: false })).working).to.equal(expected);
    workingChanged[0]!({ orgId: ORG, actorId: ALICE, runId: 'r-1', status: 'running' } as HarnessRun);
    await until(() => pushed.length === 1);
    expect(pushed).to.deep.equal([[ALICE, expected]]);
  });
});
