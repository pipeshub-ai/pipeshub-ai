/**
 * G2 (ADR-INBOX-01): the asks the Inbox reads from their own services (questions, memory reviews) are the viewer's
 * own, and a count pushed after a notice is written never fails the writer.
 */
import { expect } from 'chai';
import { InMemoryInboxStore } from '../../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import { InboxFeedService } from '../../../src/modules/agent-harness/application/inbox-feed';
import {
  MemoryReviewSource,
  QuestionWaitingSource,
} from '../../../src/modules/agent-harness/application/inbox-sources';
import type { MemoryView } from '../../../src/modules/agent-harness/domain/memory';
import type { InboxCounts } from '../../../src/modules/agent-harness/domain/inbox';
import type { InboxNotices, InboxViewer } from '../../../src/modules/agent-harness/ports/inbox';

const ANA: InboxViewer = { orgId: 'org-1', userId: 'ana', isAdmin: false, authorization: 'Bearer ana' };

const memory = (memoryId: string, status: MemoryView['status'], agentKey: string | null = null): MemoryView =>
  ({ memoryId, status, agentKey, content: `remember ${memoryId}`, createdAt: 1_000 }) as MemoryView;

describe('G2 the Inbox sources (ADR-INBOX-01)', () => {
  it("IBX-S1: questions are the viewer's own runs' unanswered ones, asked for by org and person", async () => {
    const asked: unknown[] = [];
    const source = new QuestionWaitingSource({
      pending: async (query: unknown) => {
        asked.push(query);
        return [
          { runId: 'r1', sessionId: 'c1', runUpdatedAt: 5, callId: 'k1', question: 'Which repo?', options: [], multiSelect: false, moreQuestions: 0 },
        ];
      },
    } as never);

    const items = await source.waiting(ANA, 7);

    expect(asked).to.deep.equal([{ orgId: 'org-1', actorId: 'ana', limit: 7 }]);
    expect(items.map((item) => [item.id, item.kind, item.waiting])).to.deep.equal([['q:r1:k1', 'question', true]]);
  });

  it("IBX-S2: memory reviews are the viewer's own proposals; an admin also sees the agents' proposed ones", async () => {
    const calls: string[] = [];
    const memories = {
      listOwn: async (as: { userId: string; authorization?: string }, statuses: string[]) => {
        calls.push(`own:${as.userId}:${as.authorization}:${statuses.join(',')}`);
        return [memory('mine', 'proposed')];
      },
      listAgent: async () => {
        calls.push('agent');
        return [memory('agent-proposed', 'proposed', 'helper'), memory('agent-kept', 'accepted', 'helper')];
      },
    };
    const source = new MemoryReviewSource(memories as never);

    const own = await source.waiting(ANA, 10);
    expect(own.map((item) => [item.id, item.title])).to.deep.equal([['m:mine', 'Keep this memory?']]);
    expect(calls).to.deep.equal(['own:ana:Bearer ana:proposed']);

    calls.length = 0;
    const admin = await source.waiting({ ...ANA, isAdmin: true }, 10);
    expect(admin.map((item) => item.id)).to.deep.equal(['m:mine', 'm:agent-proposed']);
    expect(admin[1]).to.deep.include({ title: 'An agent wants to remember this' });
    expect(calls).to.have.members(['own:ana:Bearer ana:proposed', 'agent']);
    expect((await source.waiting({ ...ANA, isAdmin: true }, 1)).map((item) => item.id)).to.deep.equal(['m:mine']);
  });

  it('IBX-S3: a count pushed after a notice is written reaches the person, and a failure to count is swallowed', async () => {
    let failing = false;
    const notices = {
      unreadCount: async () => {
        if (failing) throw new Error('store down');
        return 3;
      },
    } as unknown as InboxNotices;
    const pushed: Array<[string, InboxCounts]> = [];
    const feed = new InboxFeedService({
      notices,
      waiting: [],
      preferences: new InMemoryInboxStore(),
      schedules: async () => false,
      publisher: { publish: (userId, counts) => pushed.push([userId, counts]) },
    });

    await feed.announce(ANA.orgId, ANA.userId);
    expect(pushed.map(([userId, counts]) => [userId, counts.unread])).to.deep.equal([['ana', 3]]);

    failing = true;
    await feed.announce(ANA.orgId, ANA.userId);
    expect(pushed).to.have.length(1);

    await new InboxFeedService({ notices, waiting: [], preferences: new InMemoryInboxStore(), schedules: async () => false }).announce(ANA.orgId, ANA.userId);
  });
});
