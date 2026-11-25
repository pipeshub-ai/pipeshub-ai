/**
 * G2 (ADR-INBOX-01): the counts pushed after a finished-run notice is written are the person's as their role on the
 * server says, never as a caller says: an admin's push counts the asks only admins see (the agents' proposed
 * memories), a member's does not, and a role that cannot be read pushes nothing rather than a member's count.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { InMemoryInboxStore } from '../../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import { InboxFeedService } from '../../../src/modules/agent-harness/application/inbox-feed';
import { MemoryReviewSource } from '../../../src/modules/agent-harness/application/inbox-sources';
import type { HarnessBindingContext } from '../../../src/modules/agent-harness/binders/context';
import { bindInbox } from '../../../src/modules/agent-harness/binders/inbox';
import {
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_MEMORIES,
} from '../../../src/modules/agent-harness/binders/tokens';
import type { InboxCounts } from '../../../src/modules/agent-harness/domain/inbox';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { MemoryView } from '../../../src/modules/agent-harness/domain/memory';
import type { InboxNotices } from '../../../src/modules/agent-harness/ports/inbox';

const ORG = 'org-push-1';
const ADMIN = 'ben';
const MEMBER = 'ana';

const agentProposal = { memoryId: 'agent-1', status: 'proposed', agentKey: 'helper', content: 'remember', createdAt: 1 } as MemoryView;
const memories = { listOwn: async () => [], listAgent: async () => [agentProposal] };
const notices = { unreadCount: async () => 1 } as unknown as InboxNotices;

const ended = (actorId: string): HarnessRun =>
  ({
    runId: `r-${actorId}`,
    orgId: ORG,
    sessionId: `c-${actorId}`,
    actorId,
    status: 'completed',
    outcome: 'answer',
    idempotencyKey: `turn-${actorId}`,
    input: {},
  }) as HarnessRun;

async function until(done: () => boolean): Promise<void> {
  for (let i = 0; i < 200 && !done(); i += 1) await new Promise((resolve) => setImmediate(resolve));
  expect(done()).to.equal(true);
}

describe('G2 the Inbox count push follows the role the server reads (ADR-INBOX-01)', () => {
  it("IBX-RP1: a finished-run notice pushes an admin's count with the admin-only asks, a member's without", async () => {
    const container = new Container();
    const runEnded: Array<(run: HarnessRun) => void> = [];
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({ onRunWorkingChanged: () => undefined, onRunEnded: (listener: (run: HarnessRun) => void) => runEnded.push(listener), onRunAsksChanged: () => undefined });
    container.bind(AGENT_HARNESS_MEMORIES).toConstantValue(memories);
    const pushed: Array<[string, InboxCounts]> = [];
    const asked: string[] = [];
    bindInbox({
      container,
      logger: undefined,
      dependencies: {
        approvers: {
          isOrgAdmin: async (orgId: string, userId: string) => {
            asked.push(`${orgId}:${userId}`);
            return userId === ADMIN;
          },
        },
        inbox: {
          store: new InMemoryInboxStore(),
          notices,
          notifier: { runFinished: async () => undefined },
          publisher: { publish: (userId: string, counts: InboxCounts) => pushed.push([userId, counts]) },
        },
      },
    } as unknown as HarnessBindingContext);
    expect(runEnded).to.have.length(1);

    for (const actor of [MEMBER, ADMIN]) runEnded[0]!(ended(actor));
    await until(() => pushed.length === 2);

    const waiting = new Map(pushed.map(([userId, counts]) => [userId, counts.waiting]));
    expect(waiting.get(MEMBER)).to.equal(0);
    expect(waiting.get(ADMIN)).to.equal(1);
    expect(asked).to.have.members([`${ORG}:${MEMBER}`, `${ORG}:${ADMIN}`]);
  });

  it('IBX-RP2: a role that cannot be read pushes nothing; without a role check nobody is an admin', async () => {
    const pushed: Array<[string, InboxCounts]> = [];
    const feed = (isOrgAdmin?: (orgId: string, userId: string) => Promise<boolean>) =>
      new InboxFeedService({
        notices,
        waiting: [new MemoryReviewSource(memories as never)],
        preferences: new InMemoryInboxStore(),
        schedules: async () => false,
        publisher: { publish: (userId, counts) => pushed.push([userId, counts]) },
        ...(isOrgAdmin !== undefined ? { isOrgAdmin } : {}),
      });

    await feed(async () => {
      throw new Error('directory down');
    }).announce(ORG, ADMIN);
    expect(pushed).to.deep.equal([]);

    await feed().announce(ORG, ADMIN);
    expect(pushed.map(([userId, counts]) => [userId, counts.waiting])).to.deep.equal([[ADMIN, 0]]);
  });
});
