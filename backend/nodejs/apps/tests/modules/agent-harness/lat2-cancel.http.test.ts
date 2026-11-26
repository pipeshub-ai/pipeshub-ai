/**
 * LAT2 CX-1..CX-8: the cancel route API and PAT clients already use,
 * `POST …/cancel {runId}`, stops the durable run their turn was admitted as;
 * any other run id still goes to today's cancel.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { principalRefFor } from '../../../src/modules/agent-harness/domain/run-spec';
import { persistedRun } from '../../contracts/support/surfaces';
import {
  ChatTurnHarness,
  ChatTurnHarnessOptions,
  logged,
  ORG,
  sseFrames,
  startChatTurnHarness,
  USER,
} from './support/chat-turn-harness';

const TURN = '3f1c2a8e-9d0b-4c5e-8a7f-112233445566';
const OTHER_TURN = '5b7d9f1a-2c4e-4a6b-8d0f-aabbccddeeff';

describe('LAT2: the legacy cancel route reaches a durable turn', () => {
  let conversation: Record<string, unknown>;
  let http: ChatTurnHarness | undefined;

  const start = (options: ChatTurnHarnessOptions = {}) =>
    startChatTurnHarness({ conversation, publicSurface: 'web', ...options });

  before(async () => {
    conversation = (await persistedRun('chat_answer_with_citations')).conversation;
    sinon.restore();
  });

  afterEach(async () => {
    await http?.close();
    http = undefined;
    sinon.restore();
  });

  /** A durable turn sent with `runId`, still running server-side once its response ended. */
  const durableTurn = async (
    h: ChatTurnHarness,
    path = '/conversations/stream',
    identity = 'session',
    chatMode = 'internal_search',
  ): Promise<{ sessionId: string; runId: string }> => {
    const frames = sseFrames(await (await h.post(path, identity, { query: 'q', chatMode, runId: TURN })).text());
    const value = frames[0]?.data['value'] as { conversationId: string; runId: string };
    return { sessionId: value.conversationId, runId: value.runId };
  };

  const status = async (h: ChatTurnHarness, runId: string) => (await h.store.get(ORG, runId))?.status;

  it('CX-1: the turn’s own runId cancels the durable run it was admitted as; today’s cancel never runs', async () => {
    http = await start();
    const turn = await durableTurn(http);
    expect(turn.runId, 'the durable run has its own id').not.to.equal(TURN);
    const reply = await http.post(`/conversations/${turn.sessionId}/cancel`, 'session', { runId: TURN });
    expect(reply.status).to.equal(200);
    expect(await reply.json()).to.deep.equal({ cancelled: true });
    expect(await status(http, turn.runId)).to.be.oneOf(['cancelling', 'cancelled']);
    expect(http.legacy.called).to.equal(false);
    expect(logged(http.lines, 'chat_turn.cancelled')[0]).to.include({ orgId: ORG, actorId: USER, sessionId: turn.sessionId, runId: turn.runId });
  });

  it('CX-2: the durable run’s own id (from conversation_created) cancels it too', async () => {
    http = await start();
    const turn = await durableTurn(http);
    const reply = await http.post(`/conversations/${turn.sessionId}/cancel`, 'session', { runId: turn.runId });
    expect(await reply.json()).to.deep.equal({ cancelled: true });
    expect(await status(http, turn.runId)).to.be.oneOf(['cancelling', 'cancelled']);
  });

  it('CX-3: the agent twin', async () => {
    http = await start({ scenario: 'agent_quick_tool_call' });
    const turn = await durableTurn(http, '/agents/agent-1/conversations/stream', 'session', 'quick');
    const reply = await http.post(`/agents/agent-1/conversations/${turn.sessionId}/cancel`, 'session', { runId: TURN });
    expect(await reply.json()).to.deep.equal({ cancelled: true });
    expect(await status(http, turn.runId)).to.be.oneOf(['cancelling', 'cancelled']);
    expect(http.legacy.called).to.equal(false);
  });

  it('CX-3: another agent’s route never reaches the run', async () => {
    http = await start({ scenario: 'agent_quick_tool_call' });
    const turn = await durableTurn(http, '/agents/agent-1/conversations/stream', 'session', 'quick');
    await http.post(`/agents/agent-2/conversations/${turn.sessionId}/cancel`, 'session', { runId: TURN });
    expect(http.legacy.calledOnce).to.equal(true);
    expect(await status(http, turn.runId)).to.equal('queued');
  });

  describe('CX-4: a runId that names no active durable run of this conversation goes to today’s cancel', () => {
    it('a legacy turn’s id', async () => {
      http = await start();
      const turn = await durableTurn(http);
      await http.post(`/conversations/${turn.sessionId}/cancel`, 'session', { runId: OTHER_TURN });
      expect(http.legacy.calledOnce).to.equal(true);
      expect(await status(http, turn.runId)).to.equal('queued');
    });

    it('another conversation', async () => {
      http = await start();
      const turn = await durableTurn(http);
      await http.post(`/conversations/${new Types.ObjectId().toHexString()}/cancel`, 'session', { runId: TURN });
      expect(http.legacy.calledOnce).to.equal(true);
      expect(await status(http, turn.runId)).to.equal('queued');
    });

    it('a run that already ended', async () => {
      http = await start();
      const turn = await durableTurn(http);
      const run = (await http.store.get(ORG, turn.runId))!;
      await http.executions.control({ orgId: ORG, runId: run.runId, expectedRevision: run.revision, action: 'cancel', by: principalRefFor(USER), commandId: 'end', dispatchCommandId: 'end:dispatch' });
      expect(await status(http, turn.runId)).to.equal('cancelled');
      await http.post(`/conversations/${turn.sessionId}/cancel`, 'session', { runId: TURN });
      expect(http.legacy.calledOnce).to.equal(true);
    });
  });

  it('CX-11 (QA-26 order): another conversation’s running run id, sent on your own running conversation, stops neither', async () => {
    http = await start();
    const mine = await durableTurn(http);
    const frames = sseFrames(await (await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'internal_search', runId: OTHER_TURN })).text());
    const theirs = (frames[0]?.data['value'] as { runId: string }).runId;
    for (const runId of [theirs, OTHER_TURN]) {
      const reply = await http.post(`/conversations/${mine.sessionId}/cancel`, 'session', { runId });
      expect(await reply.json(), runId).to.deep.equal({ cancelled: false, legacy: true });
    }
    expect(await status(http, mine.runId)).to.equal('queued');
    expect(await status(http, theirs)).to.equal('queued');
  });

  it('CX-5: a caller who is no participant of the session never reaches the run', async () => {
    http = await start();
    const turn = await durableTurn(http);
    await http.close();
    http = await start({ noSessionAccess: true });
    await http.post(`/conversations/${turn.sessionId}/cancel`, 'session', { runId: TURN });
    expect(http.legacy.calledOnce).to.equal(true);
  });

  it('CX-5: a participant cannot stop someone else’s run', async () => {
    http = await start();
    const turn = await durableTurn(http);
    const run = (await http.store.get(ORG, turn.runId))!;
    sinon.stub(http.store, 'getActive').resolves({ ...run, actorId: new Types.ObjectId().toHexString() });
    await http.post(`/conversations/${turn.sessionId}/cancel`, 'session', { runId: TURN });
    expect(http.legacy.calledOnce).to.equal(true);
    expect(await status(http, turn.runId)).to.equal('queued');
  });

  it('CX-6: a cancel sent twice, or after the web app’s Stop, is one command and never errors', async () => {
    http = await start();
    const turn = await durableTurn(http);
    // A worker holds the run, so it stays active (cancelling) between the cancels.
    await http.executions.claim({ orgId: ORG, runId: turn.runId, workerId: 'w', attemptId: 'a', leaseDurationMs: 60_000 });
    const control = sinon.spy(http.executions, 'control');
    const replies = [
      await http.post(`/conversations/${turn.sessionId}/cancel`, 'session', { runId: TURN }),
      await http.post(`/conversations/${turn.sessionId}/cancel`, 'session', { runId: turn.runId }),
    ];
    expect(await Promise.all(replies.map((r) => r.json()))).to.deep.equal([{ cancelled: true }, { cancelled: true }]);
    expect(await status(http, turn.runId)).to.equal('cancelling');
    expect(new Set(control.getCalls().map((call) => call.args[0].commandId)), 'one command').to.have.property('size', 1);
  });

  it('CX-7: harness off: today’s cancel, untouched, even for a durable turn', async () => {
    // The harness reads `enabled` per request, as the app reads the infrastructure flag.
    const options: ChatTurnHarnessOptions = { conversation, publicSurface: 'web', enabled: true };
    http = await startChatTurnHarness(options);
    const turn = await durableTurn(http);
    options.enabled = false;
    await http.post(`/conversations/${turn.sessionId}/cancel`, 'session', { runId: TURN });
    expect(http.legacy.calledOnce).to.equal(true);
    expect(await status(http, turn.runId)).to.equal('queued');
  });

  it('CX-8: a PAT client stops its own turn with the run id it sent', async () => {
    http = await start();
    const turn = await durableTurn(http, '/conversations/stream', 'pat');
    const reply = await http.post(`/conversations/${turn.sessionId}/cancel`, 'pat', { runId: TURN });
    expect(await reply.json()).to.deep.equal({ cancelled: true });
    expect(logged(http.lines, 'chat_turn.cancelled')[0]).to.include({ via: 'pat' });
  });
});
