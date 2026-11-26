/**
 * CARDFAIL-1: a card's answer the web app sends again after a failure is the same turn (its body `runId`, kept across
 * retries) and is admitted once. While the app restarted, answers to a question card got 503
 * RUN_CONFIG_UNAVAILABLE with nothing written; the card now retries them, so a retry must never answer twice.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { HarnessRunConfigUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import { persistedRun } from '../../contracts/support/surfaces';
import { ChatTurnHarness, logged, ORG, sseFrames, startChatTurnHarness, USER } from './support/chat-turn-harness';

describe('CARDFAIL-1: an answer sent again is answered once', () => {
  let conversation: Record<string, unknown>;
  let http: ChatTurnHarness | undefined;
  const runId = '0b7c2a1e-4d5f-4a6b-9c8d-7e6f5a4b3c2d';
  const answer = { query: 'User selections:\n- Which launch?: Orbit GA', chatMode: 'internal_search', protocol: 'agui', runId };

  before(async () => {
    conversation = (await persistedRun('chat_answer_with_citations')).conversation;
    sinon.restore();
  });

  afterEach(async () => {
    await http?.close();
    http = undefined;
    sinon.restore();
  });

  it('CF-N1: refused 503 with nothing written, then the same answer again: one run, and a third send replays it', async () => {
    http = await startChatTurnHarness({ conversation, publicSurface: 'web' });
    const admit = sinon.stub(http.store, 'admit').callThrough();
    admit.onFirstCall().rejects(new HarnessRunConfigUnavailableError());
    const send = () => http!.post('/conversations/stream', 'session', answer);

    const refused = await send();
    expect(refused.status).to.equal(503);
    expect(JSON.stringify(await refused.json())).to.contain('HTTP_AGENT_HARNESS_RUN_CONFIG_UNAVAILABLE');
    expect(logged(http.lines, 'admission.accepted'), 'nothing was admitted').to.deep.equal([]);

    const first = sseFrames(await (await send()).text());
    // The reply to the retry was lost, so the card sends the same answer a third time.
    const again = sseFrames(await (await send()).text());
    const handle = first[0]?.data['value'] as { runId?: string; conversationId?: string };
    expect(handle?.runId).to.be.a('string');
    expect(again[0]?.data['value'], 'the third send streams the first run').to.deep.equal(handle);
    expect(logged(http.lines, 'admission.accepted').map((line) => line['replayed'])).to.deep.equal([false, true]);

    const runs = await http.store.activeRunsOfActor(ORG, USER);
    expect(runs.map((run) => run.runId), 'one run for the three sends').to.deep.equal([handle.runId]);
    expect(http.legacy.called, "today's loop never answered it").to.equal(false);
  });

  it('CF-N2: a different answer is a different turn id, so it is never taken for the first', async () => {
    http = await startChatTurnHarness({ conversation, publicSurface: 'web' });
    const first = sseFrames(await (await http.post('/conversations/stream', 'session', answer)).text());
    const other = sseFrames(
      await (
        await http.post('/conversations/stream', 'session', { ...answer, runId: '9f8e7d6c-5b4a-4c3d-8e2f-1a0b9c8d7e6f' })
      ).text(),
    );
    const a = first[0]?.data['value'] as { runId?: string };
    const b = other[0]?.data['value'] as { runId?: string };
    expect(a.runId).to.be.a('string').and.not.equal(b.runId);
    expect(logged(http.lines, 'admission.accepted').map((line) => line['replayed'])).to.deep.equal([false, false]);
  });
});
