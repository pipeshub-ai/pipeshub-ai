/**
 * WP62-RUNID: the `runId` a client sends on a chat turn is the turn's handle, never the run's id. The server names
 * the run (conversation_created carries it); the client's id names the turn for a retry and for
 * `POST …/cancel {runId}`, and only for the caller who sent it: another user, or another org, sending the same id
 * gets a run of its own and can stop nothing of the first caller's.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { persistedRun } from '../../contracts/support/surfaces';
import {
  ChatTurnHarness,
  OTHER_ORG,
  ORG,
  sseFrames,
  startChatTurnHarness,
} from './support/chat-turn-harness';

const CLIENT_RUN = 'b9216dbc-52ba-4497-b4bf-49743fc4b892';

describe('WP62-RUNID: a client runId is its sender’s turn handle', () => {
  let conversation: Record<string, unknown>;
  let http: ChatTurnHarness | undefined;

  before(async () => {
    conversation = (await persistedRun('chat_answer_with_citations')).conversation;
    sinon.restore();
  });

  afterEach(async () => {
    await http?.close();
    http = undefined;
    sinon.restore();
  });

  const turn = async (identity: string): Promise<{ sessionId: string; runId: string }> => {
    const frames = sseFrames(
      await (await http!.post('/conversations/stream', identity, { query: 'q', chatMode: 'internal_search', runId: CLIENT_RUN })).text(),
    );
    const value = frames[0]?.data['value'] as { conversationId: string; runId: string };
    return { sessionId: value.conversationId, runId: value.runId };
  };

  it('WP62-RUNID-1: the server names the run; the same request sent again is the same turn', async () => {
    http = await startChatTurnHarness({ conversation, publicSurface: 'web' });
    const first = await turn('session');
    expect(first.runId).to.be.a('string').and.not.equal(CLIENT_RUN);
    expect(await turn('session')).to.deep.equal(first);
  });

  it('WP62-RUNID-2: another user, or another org, sending the same runId gets a run of its own', async () => {
    http = await startChatTurnHarness({ conversation, publicSurface: 'web' });
    const mine = await turn('session');
    const colleague = await turn('otherUser');
    const stranger = await turn('otherOrg');
    expect(new Set([mine.runId, colleague.runId, stranger.runId]).size).to.equal(3);
    expect(new Set([mine.sessionId, colleague.sessionId, stranger.sessionId]).size).to.equal(3);
    expect((await http.store.get(ORG, mine.runId))?.status).to.equal('queued');
    expect(await http.store.get(ORG, stranger.runId), 'never under the first caller’s org').to.equal(null);
    expect((await http.store.get(OTHER_ORG, stranger.runId))?.status).to.equal('queued');
  });

  it('WP62-RUNID-3: the shared runId stops only its sender’s run, on its sender’s conversation', async () => {
    http = await startChatTurnHarness({ conversation, publicSurface: 'web' });
    const mine = await turn('session');
    const stranger = await turn('otherOrg');
    // Another org's member names the first caller's conversation with the shared id: it reaches nothing durable.
    await http.post(`/conversations/${mine.sessionId}/cancel`, 'otherOrg', { runId: CLIENT_RUN });
    expect((await http.store.get(ORG, mine.runId))?.status).to.equal('queued');
    const reply = await http.post(`/conversations/${stranger.sessionId}/cancel`, 'otherOrg', { runId: CLIENT_RUN });
    expect(await reply.json()).to.deep.equal({ cancelled: true });
    expect((await http.store.get(OTHER_ORG, stranger.runId))?.status).to.be.oneOf(['cancelling', 'cancelled']);
    expect((await http.store.get(ORG, mine.runId))?.status).to.equal('queued');
  });
});
