/**
 * GA-02..GA-04 over HTTP: a turn's `gates` reach admission and the run; a gated turn the harness does not serve is
 * refused (400 `gates_need_harness`), never handed to today's loop ungated (a dry run there would act for real);
 * gates that cannot be read are refused before anything runs.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { SURFACE_HEADER } from '../../../src/modules/agent-harness/transport/surface-header';
import { persistedRun } from '../../contracts/support/surfaces';
import {
  ChatTurnHarness as Harness,
  logged,
  ORG,
  startChatTurnHarness,
} from './support/chat-turn-harness';

const MCP = { [SURFACE_HEADER]: 'mcp' };
const TURN = { query: 'Send the report to finance', chatMode: 'internal_search' };

describe('GA-02..GA-04: a gated chat turn over HTTP', () => {
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

  it('the gates reach the run, and today\'s loop never sees the field', async () => {
    http = await startChatTurnHarness({ conversation });
    const reply = await http.post('/conversations/stream', 'pat', { ...TURN, gates: { dryRun: true } }, MCP);
    expect(reply.status).to.equal(200);
    await reply.text();
    expect(http.legacy.called).to.equal(false);
    const submitted = http.submit.firstCall.args[0] as Record<string, unknown>;
    expect(submitted['gates']).to.deep.equal({ dryRun: true });
    const [accepted] = logged(http.lines, 'admission.accepted');
    const run = (await http.store.get(ORG, String(accepted!['runId'])))!;
    expect(run.requires?.features).to.include('run_gates');
    expect((run.input as unknown as Record<string, unknown>)['gates']).to.equal(undefined);
  });

  it('a gated turn the harness does not serve is refused, never run ungated on legacy', async () => {
    http = await startChatTurnHarness({ enabled: false });
    const reply = await http.post('/conversations/stream', 'pat', { ...TURN, gates: { dryRun: true } }, MCP);
    expect(reply.status).to.equal(400);
    expect(await reply.text()).to.contain('gates_need_harness');
    expect(http.legacy.called).to.equal(false);
  });

  it('gates that cannot be read are refused before anything runs', async () => {
    http = await startChatTurnHarness({ conversation });
    const reply = await http.post('/conversations/stream', 'pat', { ...TURN, gates: { dryRun: false } }, MCP);
    expect(reply.status).to.equal(400);
    expect(http.submit.called).to.equal(false);
    expect(http.legacy.called).to.equal(false);
  });
});
