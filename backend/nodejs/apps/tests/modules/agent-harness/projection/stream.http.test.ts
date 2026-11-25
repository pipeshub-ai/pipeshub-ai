/**
 * The durable stream route over real HTTP (L-15, L-1, L-7): the session
 * router with the real scope checks and error middleware, the real
 * SessionRunService deciding who may watch, and the stream service over a
 * finished golden run.
 */
import { expect } from 'chai';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { parseSse, readJournal } from '../../../contracts/support/durable';
import { InMemoryRecordReadAccess } from '../../../../src/modules/agent-harness/adapters/in-memory-record-access';
import { indexed, leaksOf, recordCitation, withCitations } from '../support/citations';
import { Served, startStreamHttp, streamRun } from '../support/stream-http';

const cited = [recordCitation('rec-open', 1), recordCitation('rec-closed', 2)];
const journal = withCitations(readJournal('agent_quick_tool_call'), cited);
const records = new InMemoryRecordReadAccess().grant('Bearer viewer', 'rec-open');

const run = streamRun(journal);
const start = (served: Served = { journal, run, records, citations: cited, chat: false }) => startStreamHttp(served);

const PATH = `/agents/agent-1/conversations/${journal.sessionId}/runs/${journal.runId}/stream`;

describe('agent-harness projection: the stream route over HTTP', () => {
  let http: Awaited<ReturnType<typeof start>>;
  beforeEach(async () => {
    http = await start();
  });
  afterEach(async () => {
    await http.close();
  });

  it('streams the owner the whole run as SSE with ids, and ends the response', async () => {
    const reply = await http.get(PATH, 'owner');
    expect(reply.status).to.equal(200);
    expect(reply.headers.get('content-type')).to.equal('text/event-stream');
    expect(reply.headers.get('access-control-allow-origin'), 'the manual ACAO: * is gone (REPORT 8)').to.equal(null);
    const frames = parseSse(await reply.text());
    expect(frames.every((f) => typeof f.id === 'string')).to.equal(true);
    expect(frames[0]!.frame).to.deep.include({ type: 'CUSTOM', name: 'conversation_created' });
    expect(frames.at(-1)!.frame.type).to.equal('RUN_FINISHED');
    expect(frames.some((f) => f.frame.type === 'TOOL_CALL_START')).to.equal(true);
  });

  it('L-1: resumes after Last-Event-ID (header or query) with exactly the rest', async () => {
    const clockless = (frames: ReturnType<typeof parseSse>) =>
      JSON.parse(JSON.stringify(frames).replace(/"timestamp":"[^"]+"/g, '"timestamp":"<clock>"')) as unknown;
    const whole = parseSse(await (await http.get(PATH, 'owner')).text());
    const cut = 7;
    const rest = parseSse(await (await http.get(PATH, 'owner', { 'Last-Event-ID': whole[cut]!.id! })).text());
    expect(clockless(rest)).to.deep.equal(clockless(whole.slice(cut + 1)));
    const viaQuery = parseSse(await (await http.get(`${PATH}?lastEventId=${encodeURIComponent(whole[cut]!.id!)}`, 'owner')).text());
    expect(clockless(viaQuery)).to.deep.equal(clockless(whole.slice(cut + 1)));
  });

  it('L-7: a user the conversation is shared with gets the shared audience', async () => {
    const frames = parseSse(await (await http.get(PATH, 'viewer')).text()).map((f) => f.frame.type);
    expect(frames).to.not.include('TOOL_CALL_START');
    expect(frames).to.not.include('TEXT_MESSAGE_CONTENT');
    expect(frames.at(-1)).to.equal('RUN_FINISHED');
  });

  it('C-6: the shared viewer\'s own credential decides which citations they see; the owner is not checked', async () => {
    records.queries.length = 0;
    const owner = await (await http.get(PATH, 'owner')).text();
    expect(records.queries, 'the owner is never checked').to.deep.equal([]);
    expect(owner).to.include('NAME-rec-closed');
    const shared = await (await http.get(PATH, 'viewer')).text();
    expect(records.queries).to.deep.equal([{ authorization: 'Bearer viewer', sources: [indexed('rec-open'), indexed('rec-closed')] }]);
    expect(shared).to.include('NAME-rec-open');
    for (const leak of leaksOf('rec-closed')) expect(shared, leak).to.not.include(leak);
  });

  it('L-15: 404 for a caller who neither owns nor shares the conversation, and for another session\'s run', async () => {
    expect((await http.get(PATH, 'stranger')).status).to.equal(404);
    expect((await http.get(`/agents/agent-1/conversations/other-session/runs/${journal.runId}/stream`, 'owner')).status).to.equal(404);
  });

  it('L-15: the read scope is required (an agent:execute-only token is refused)', async () => {
    expect((await http.get(PATH, 'exec-only-pat')).status).to.equal(403);
  });
});

describe('agent-harness projection: a built-in assistant run over HTTP (T1a, C-6 for plain chat)', () => {
  const chatJournal = readJournal('chat_answer_with_citations');
  const chatRun = { ...run, runId: chatJournal.runId, sessionId: chatJournal.sessionId, agentKey: undefined } as unknown as HarnessRun;
  const chatPath = `/conversations/${chatJournal.sessionId}/runs/${chatJournal.runId}/stream`;
  const readable = new InMemoryRecordReadAccess();
  let http: Awaited<ReturnType<typeof start>>;
  beforeEach(async () => {
    readable.queries.length = 0;
    http = await start({ journal: chatJournal, run: chatRun, records: readable, citations: [], chat: true });
  });
  afterEach(async () => {
    await http.close();
  });

  it('streams the owner the whole answer, citations included, and never checks them', async () => {
    const owner = await (await http.get(chatPath, 'owner')).text();
    expect(readable.queries).to.deep.equal([]);
    const frames = parseSse(owner);
    expect(frames[0]!.frame).to.deep.include({ type: 'CUSTOM', name: 'conversation_created' });
    expect(frames.at(-1)!.frame.type).to.equal('RUN_FINISHED');
    expect(owner).to.include('Customers may request a refund within 30 days of purchase.');
  });

  it('a shared viewer sees a cited record only when their own credential can read it', async () => {
    const denied = await (await http.get(chatPath, 'viewer')).text();
    expect(readable.queries).to.deep.equal([{ authorization: 'Bearer viewer', sources: [indexed('rec-refund')] }]);
    for (const leak of ['Customers may request a refund within 30 days of purchase.', 'Refund policy.md', 'vr-refund']) {
      expect(denied, leak).to.not.include(leak);
    }
    expect(denied).to.include('"unavailable":true');
    expect(parseSse(denied).map((f) => f.frame.type)).to.not.include('TEXT_MESSAGE_CONTENT');

    readable.grant('Bearer viewer', 'rec-refund');
    const allowed = await (await http.get(chatPath, 'viewer')).text();
    expect(allowed).to.include('Customers may request a refund within 30 days of purchase.');
  });
});
