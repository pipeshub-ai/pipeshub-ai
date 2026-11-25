/**
 * WP-64 over the chat routes (the chat's question card): a message that answers a gated run's question is never served
 * by today's loop, which has no gates, whatever hands the turn there (a body the harness cannot take, admission
 * unavailable, a rollout that keeps the org on today's loop, the harness turned off for the workspace). It is refused
 * with 409 `GATES_NEED_RUNTIME`, or 503 when the check cannot be read (fail closed); an answer to an ungated run
 * still falls back as before.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { HarnessUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import { SURFACE_HEADER } from '../../../src/modules/agent-harness/transport/surface-header';
import { persistedRun } from '../../contracts/support/surfaces';
import { ChatTurnHarness as Harness, startChatTurnHarness } from './support/chat-turn-harness';

const MCP = { [SURFACE_HEADER]: 'mcp' };
const ANSWER = { query: 'User selections:\n1. "Which region?" → EU', chatMode: 'internal_search' };
const ASKED = { runId: 'run-asked', gates: { dryRun: true as const } };

describe('WP-64: an answer to a gated run over the chat routes', () => {
  let http: Harness | undefined;
  let conversation: Record<string, unknown>;

  before(async () => {
    conversation = (await persistedRun('chat_answer_with_citations')).conversation;
  });

  afterEach(async () => {
    await http?.close();
    http = undefined;
    sinon.restore();
  });

  it('WP64-RT-6: a body the harness cannot take is refused, not handed to today’s loop ungated', async () => {
    http = await startChatTurnHarness({ conversation });
    const read = sinon.stub(http.store, 'answeredRun').resolves(ASKED);
    const reply = await http.post('/conversations/c-1/messages/stream', 'pat', { ...ANSWER, unknownField: 1 }, MCP);
    expect(reply.status).to.equal(409);
    expect(await reply.text()).to.contain('GATES_NEED_RUNTIME');
    expect(http.legacy.called).to.equal(false);
    expect(read.firstCall.args.slice(1)).to.deep.equal(['c-1']);
  });

  it('WP64-RT-7: admission unavailable does not hand a gated answer to today’s loop', async () => {
    http = await startChatTurnHarness({ conversation, admitFails: new HarnessUnavailableError('down') });
    sinon.stub(http.store, 'answeredRun').resolves(ASKED);
    const reply = await http.post('/conversations/c-1/messages/stream', 'pat', ANSWER, MCP);
    expect(reply.status).to.equal(409);
    expect(http.legacy.called).to.equal(false);
  });

  it('WP64-RT-8: an org the rollout keeps on today’s loop is refused a gated answer in plain words, never an ungated run', async () => {
    http = await startChatTurnHarness({ conversation, settings: { orgEnabled: false } });
    sinon.stub(http.store, 'answeredRun').resolves(ASKED);
    const reply = await http.post('/conversations/c-1/messages/stream', 'pat', ANSWER, MCP);
    expect(reply.status).to.equal(409);
    expect(await reply.text()).to.contain('need the agent runtime, which is turned off for this workspace');
    expect(http.legacy.called).to.equal(false);
  });

  it('WP64-RT-9: an answer to an ungated run, or a check that cannot be made, behaves as named', async () => {
    http = await startChatTurnHarness({ conversation });
    const read = sinon.stub(http.store, 'answeredRun').resolves({ runId: 'run-asked' });
    const fallback = await http.post('/conversations/c-1/messages/stream', 'pat', { ...ANSWER, unknownField: 1 }, MCP);
    expect(await fallback.text()).to.equal('legacy');
    read.rejects(new Error('mongo down'));
    const unchecked = await http.post('/conversations/c-1/messages/stream', 'pat', { ...ANSWER, unknownField: 1 }, MCP);
    expect(unchecked.status).to.equal(503);
    expect(http.legacy.callCount).to.equal(1);
  });

  it('WP64-RT-12: the harness turned off for the workspace still refuses an answer to a gated run (fail closed)', async () => {
    http = await startChatTurnHarness({ conversation, enabled: false });
    const read = sinon.stub(http.store, 'answeredRun').resolves(ASKED);
    const reply = await http.post('/conversations/c-1/messages/stream', 'pat', ANSWER, MCP);
    expect(reply.status).to.equal(409);
    expect(await reply.text()).to.contain('GATES_NEED_RUNTIME');
    expect(http.legacy.called).to.equal(false);
    expect(read.calledOnce).to.equal(true);
  });

  it('WP64-RT-13: harness off, a check that cannot be read refuses (503); an ungated answer and a new chat go on as before', async () => {
    http = await startChatTurnHarness({ conversation, enabled: false });
    const read = sinon.stub(http.store, 'answeredRun').rejects(new Error('mongo down'));
    expect((await http.post('/conversations/c-1/messages/stream', 'pat', ANSWER, MCP)).status).to.equal(503);
    read.resolves({ runId: 'run-asked' });
    expect(await (await http.post('/conversations/c-1/messages/stream', 'pat', ANSWER, MCP)).text()).to.equal('legacy');
    read.resetHistory();
    expect(await (await http.post('/conversations/stream', 'pat', ANSWER, MCP)).text()).to.equal('legacy');
    expect(read.called, 'a new chat answers nothing').to.equal(false);
  });
});
