/**
 * UI-B1 x UI-B2 (ADR-UI-01 D2, ADR-INBOX-01): opening a conversation (`PUT …/seen`) also reads that conversation's
 * finished-run notices in the person's Inbox, so the badge and the chat list agree without a second call.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { RepositoryInboxNotices } from '../../../src/libs/repositories/agent-harness/inbox/notices';
import { InMemoryConversationSeenMarks } from '../../../src/modules/agent-harness/adapters/in-memory-conversation-seen';
import { InMemoryInboxStore } from '../../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import { InboxFeedService } from '../../../src/modules/agent-harness/application/inbox-feed';
import { RunListService } from '../../../src/modules/agent-harness/application/run-lists';
import {
  AGENT_HARNESS_INBOX,
  AGENT_HARNESS_ORG_ADMIN_CHECK,
  AGENT_HARNESS_RUN_LISTS,
} from '../../../src/modules/agent-harness/binders/tokens';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import type { InboxCounts } from '../../../src/modules/agent-harness/domain/inbox';
import type { InboxViewer } from '../../../src/modules/agent-harness/ports/inbox';
import type { RunLists } from '../../../src/modules/agent-harness/ports/run-lists';
import { BackgroundAgentController } from '../../../src/modules/agent-harness/transport/background-agent-controller';
import { FakeNotifications } from './support/fake-notifications';

const ORG = '6650a1b2c3d4e5f607182930';
const ANA = '6650a1b2c3d4e5f607182940';

const noRuns: RunLists = {
  personRuns: async () => [],
  openRunsOfSessions: async () => [],
  lastEndedRuns: async () => [],
  sessionJobs: async () => [],
};

function world(opts: { allowed?: boolean; inbox?: boolean } = {}) {
  const repo = new FakeNotifications();
  const published: Array<{ userId: string; counts: InboxCounts }> = [];
  const askedBy: InboxViewer[] = [];
  const container = new Container();
  container.bind(AGENT_HARNESS_RUN_LISTS).toConstantValue(
    new RunListService({
      runs: noRuns,
      seen: new InMemoryConversationSeenMarks(),
      access: {
        assertAccess: async () => {
          if (opts.allowed === false) throw new HarnessNotFoundError('Conversation not found');
        },
      },
      now: () => 9_000,
    }),
  );
  container.bind(AGENT_HARNESS_ORG_ADMIN_CHECK).toConstantValue(async () => true);
  if (opts.inbox !== false) {
    container.bind(AGENT_HARNESS_INBOX).toConstantValue(
      new InboxFeedService({
        notices: new RepositoryInboxNotices(() => repo),
        waiting: [{ name: 'approvals', waiting: async (viewer) => (askedBy.push(viewer), []) }],
        preferences: new InMemoryInboxStore(),
        schedules: async () => false,
        publisher: { publish: (userId, counts) => published.push({ userId, counts }) },
      }),
    );
  }
  const finished = (conversationId: string) =>
    repo.seed(
      { orgId: ORG, userId: ANA },
      { createdAt: new Date(2_000), type: 'AGENT_RUN_FINISHED', title: 'Report', payload: { conversationId } },
    );
  return { repo, container, published, askedBy, finished };
}

async function openChat(container: Container, sessionId: string) {
  const req = {
    user: { orgId: ORG, userId: ANA },
    params: { sessionId },
    headers: { authorization: 'Bearer token-of-caller' },
  };
  let status = 0;
  let body: unknown;
  let failure: unknown;
  const res = {
    status(code: number) {
      status = code;
      return this;
    },
    json(value: unknown) {
      body = value;
      return this;
    },
  };
  await new BackgroundAgentController(container, 'chat').seen(req as never, res as never, (error?: unknown) => {
    failure = error;
  });
  return { status, body, failure };
}

describe('UI-B1 x UI-B2: opening a chat reads its finished-run notices', () => {
  it("SEEN-INBOX-1: the seen mark reads that conversation's unread notices only, and pushes the new counts", async () => {
    const w = world();
    const here = w.finished('c1');
    const elsewhere = w.finished('c2');

    const answer = await openChat(w.container, 'c1');

    expect(answer.failure).to.equal(undefined);
    expect(answer).to.deep.include({ status: 200, body: { seenUpTo: 9_000 } });
    expect(here.status).to.equal('read');
    expect(elsewhere.status).to.equal('unread');
    expect(w.published.map((p) => [p.userId, p.counts.unread])).to.deep.equal([[ANA, 1]]);
    // The Inbox sees the caller as its own routes do: the verified person, their admin answer, their own credential.
    expect(w.askedBy[0]).to.deep.equal({ orgId: ORG, userId: ANA, isAdmin: true, authorization: 'Bearer token-of-caller' });
  });

  it('SEEN-INBOX-2: a conversation the caller may not open moves no mark and reads no notice', async () => {
    const w = world({ allowed: false });
    const here = w.finished('c1');

    const answer = await openChat(w.container, 'c1');

    expect(answer.failure).to.be.instanceOf(Error);
    expect(here.status).to.equal('unread');
    expect(w.published).to.deep.equal([]);
  });

  it('SEEN-INBOX-3: a deployment without the Inbox still keeps the seen mark', async () => {
    const w = world({ inbox: false });

    const answer = await openChat(w.container, 'c1');

    expect(answer.failure).to.equal(undefined);
    expect(answer).to.deep.include({ status: 200, body: { seenUpTo: 9_000 } });
  });
});
