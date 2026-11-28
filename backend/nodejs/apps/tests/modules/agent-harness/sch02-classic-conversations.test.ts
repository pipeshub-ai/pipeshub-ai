/**
 * ADR-SCH-02 D3 and ADR-SCH-03 D3, through the conversation repository: a classic firing's conversation as the runner
 * reads it, an expired ask recorded on it, and whether a chat is the caller's own. Never another org's or person's.
 */
import { expect } from 'chai';
import {
  RepositoryChatConversations,
  RepositoryClassicConversations,
} from '../../../src/modules/agent-harness/adapters/classic-schedule-conversations';
import { ASK_EXPIRED } from '../../../src/modules/agent-harness/application/classic-schedule-runner';

const ORG = 'org-1';
const ALICE = 'alice';

function repository(sessions: Record<string, Record<string, unknown>>, messages: Record<string, unknown[]> = {}) {
  const recorded: Array<[string, Record<string, unknown>]> = [];
  return {
    recorded,
    repo: {
      findById: async (id: string) => sessions[id] ?? null,
      listMessages: async (id: string) => (messages[id] ?? []).map((dto) => ({ dto })),
      recordRunState: async (id: string, state: Record<string, unknown>) => {
        recorded.push([id, state]);
      },
    } as never,
  };
}

describe('the classic schedule conversations', () => {
  it('reads a deleted conversation as deleted, another org\'s as none, and an answer\'s parts and citations', async () => {
    const at = new Date('2026-11-28T10:00:00Z');
    const { repo } = repository(
      {
        gone: { orgId: ORG, isDeleted: true, createdAt: at, updatedAt: at },
        theirs: { orgId: 'org-2', status: 'Complete', createdAt: at },
        stopped: { orgId: ORG, status: 'Stopped', createdAt: at, lastActivityAt: at },
        failed: { orgId: ORG, status: 'Failed', failReason: 'stream_error', createdAt: at, updatedAt: at },
      },
      { stopped: [{ messageType: 'user_query' }, { messageType: 'bot_response', parts: [{ type: 'tool_call' }], citations: [{}, {}] }] },
    );
    const conversations = new RepositoryClassicConversations(() => repo);
    expect(await conversations.read(ORG, 'gone')).to.deep.include({ status: 'deleted', answer: null, updatedAt: at.getTime() });
    expect(await conversations.read(ORG, 'theirs')).to.equal(null);
    expect(await conversations.read(ORG, 'missing')).to.equal(null);
    expect(await conversations.read(ORG, 'stopped')).to.deep.include({
      status: 'stopped',
      answer: { parts: [{ type: 'tool_call' }], citations: 2 },
      updatedAt: at.getTime(),
    });
    expect(await conversations.read(ORG, 'failed')).to.deep.include({ status: 'failed', failReason: 'stream_error', answer: null });
  });

  it('records an expired ask on its own org\'s conversation only', async () => {
    const { repo, recorded } = repository({ mine: { orgId: ORG }, theirs: { orgId: 'org-2' } });
    const conversations = new RepositoryClassicConversations(() => repo);
    await conversations.expire(ORG, 'mine');
    await conversations.expire(ORG, 'theirs');
    await conversations.expire(ORG, 'missing');
    expect(recorded).to.deep.equal([['mine', { status: 'Failed', failReason: ASK_EXPIRED }]]);
  });

  it('a chat is the caller\'s own only in their org, not deleted, with its agent', async () => {
    const { repo } = repository({
      agent: { orgId: ORG, userId: ALICE, agentKey: 'helper' },
      assistant: { orgId: ORG, userId: ALICE },
      bobs: { orgId: ORG, userId: 'bob' },
      deleted: { orgId: ORG, userId: ALICE, isDeleted: true },
      elsewhere: { orgId: 'org-2', userId: ALICE },
    });
    const chats = new RepositoryChatConversations(() => repo);
    expect(await chats.ownedBy(ORG, ALICE, 'agent')).to.deep.equal({ agentKey: 'helper' });
    expect(await chats.ownedBy(ORG, ALICE, 'assistant')).to.deep.equal({ agentKey: null });
    for (const id of ['bobs', 'deleted', 'elsewhere', 'missing']) expect(await chats.ownedBy(ORG, ALICE, id), id).to.equal(null);
  });
});
