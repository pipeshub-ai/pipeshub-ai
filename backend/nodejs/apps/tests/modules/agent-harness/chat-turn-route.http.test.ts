/**
 * P1b C-5 over real HTTP: an external surface's turn (MCP, Slack) in front of
 * the legacy stream handler, and the published MCP parser reading the
 * response (`support/chat-turn-harness.ts`).
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { ChatTurnService } from '../../../src/modules/agent-harness/application/chat-turns';
import { chatTurnInput } from '../../../src/modules/agent-harness/transport/chat-turn-route';
import { SURFACE_HEADER } from '../../../src/modules/agent-harness/transport/surface-header';
import { McpParser, loadMcpParser, mcpToolResult, persistedRun, slackRendering } from '../../contracts/support/surfaces';
import {
  ChatTurnHarness as Harness,
  ChatTurnHarnessOptions,
  logged,
  ORG,
  SLACK_EMAIL,
  startChatTurnHarness,
  USER,
} from './support/chat-turn-harness';

const start = (options: ChatTurnHarnessOptions = {}): Promise<Harness> => startChatTurnHarness(options);

const MCP = { [SURFACE_HEADER]: 'mcp' };

describe('agent-harness: external surfaces on the durable route (C-5) over HTTP', () => {
  let parser: McpParser;
  let conversation: Record<string, unknown>;
  let http: Harness | undefined;

  before(async () => {
    parser = await loadMcpParser();
    conversation = (await persistedRun('chat_answer_with_citations')).conversation;
    sinon.restore();
  });

  afterEach(async () => {
    await http?.close();
    http = undefined;
    sinon.restore();
  });

  it('SF-1: rollout on, an MCP chat turn is admitted and streamed in the same response; the MCP parser reads the answer', async () => {
    http = await start({ conversation });
    const reply = await http.post('/conversations/stream', 'pat', { query: 'What is the refund policy?', chatMode: 'internal_search' }, MCP);
    expect(reply.status).to.equal(200);
    expect(reply.headers.get('content-type')).to.equal('text/event-stream');
    expect(reply.headers.get('access-control-allow-origin'), 'no manual CORS header').to.equal(null);
    const body = await reply.text();
    const tool = await mcpToolResult(parser, body);
    expect(tool.terminal).to.equal('RUN_FINISHED');
    expect((tool.result as Record<string, unknown>)['answer']).to.contain('Refunds are accepted within 30 days');
    expect(body).to.not.contain('TEXT_MESSAGE');
    expect(http.legacy.called, 'the legacy handler never ran').to.equal(false);
    const [accepted] = logged(http.lines, 'admission.accepted');
    expect(accepted).to.include({ orgId: ORG, actorId: USER, via: 'pat', surface: 'mcp', chatMode: 'internal_search', assistant: true });
    const run = (await http.store.get(ORG, String(accepted!['runId'])))!;
    expect(run.input?.query).to.equal('What is the refund policy?');
    expect(logged(http.lines, 'projection.subscribe')[0]).to.include({ audience: 'external' });
  });

  it('SF-1: rollout off, the legacy handler serves the turn untouched and the harness reads nothing', async () => {
    http = await start({ enabled: false });
    const reply = await http.post('/conversations/stream', 'pat', { query: 'q', chatMode: 'internal_search' }, MCP);
    expect(await reply.text()).to.equal('legacy');
    expect(http.submit.called).to.equal(false);
    // QA6-F11: the one line that says why today's loop served it.
    expect(http.lines.map(([, message, meta]) => [message, meta['reason']])).to.deep.equal([
      ['admission.legacy', 'infrastructure_unavailable'],
    ]);
  });

  it('SYNCMAIN2: a token limited to some knowledge keeps its turn on legacy, which holds it to the limit (main #4451)', async () => {
    http = await start({ conversation });
    const reply = await http.post('/conversations/stream', 'patLimited', { query: 'q', chatMode: 'internal_search' }, MCP);
    expect(await reply.text()).to.equal('legacy');
    expect(http.submit.called, 'no durable run is admitted').to.equal(false);
    expect(logged(http.lines, 'admission.legacy')[0]).to.include({ via: 'pat', surface: 'mcp', reason: 'surface_request_not_supported' });
  });

  it('SF-1: an org the rollout keeps on legacy writes nothing, reads no earlier admission, and logs why', async () => {
    http = await start({ settings: { orgEnabled: false } });
    const find = sinon.spy(http.store, 'findAdmission');
    const reply = await http.post('/conversations/stream', 'pat', { query: 'q', chatMode: 'internal_search' }, MCP);
    expect(await reply.text()).to.equal('legacy');
    expect(find.called).to.equal(false);
    expect(logged(http.lines, 'admission.legacy')[0]).to.include({ via: 'pat', surface: 'mcp', reason: 'org_disabled' });
  });

  it('a request that is not from a surface goes to the legacy handler without touching the harness', async () => {
    http = await start();
    expect(await (await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'internal_search' })).text()).to.equal('legacy');
    expect(await (await http.post('/conversations/stream', 'session', { query: 'q' }, { [SURFACE_HEADER]: 'slack' })).text()).to.equal('legacy');
    expect(http.submit.called).to.equal(false);
  });

  it('SF-5: org and actor come from the credential; a body naming them keeps the turn on legacy', async () => {
    http = await start({ conversation });
    for (const forged of [{ orgId: 'org-attacker' }, { userId: 'user-attacker' }, { via: 'session' }]) {
      const reply = await http.post('/conversations/stream', 'pat', { query: 'q', chatMode: 'internal_search', ...forged }, MCP);
      expect(await reply.text(), JSON.stringify(forged)).to.equal('legacy');
    }
    expect(http.submit.called).to.equal(false);
    const declined = logged(http.lines, 'admission.legacy');
    expect(declined).to.have.length(3);
    for (const line of declined) {
      expect(line).to.include({ orgId: ORG, actorId: USER, via: 'pat', surface: 'mcp', reason: 'surface_request_not_supported' });
      expect(JSON.stringify(line)).to.not.contain('attacker');
    }
  });

  it('SF-5: a Slack service account runs as the hydrated identity, via service, with the caller from the token', async () => {
    http = await start({ conversation, scenario: 'agent_quick_tool_call' });
    const reply = await http.post('/agents/agent-1/conversations/internal/stream', 'service', {
      query: 'How much paid leave do I get?',
      chatMode: 'quick',
      callerDisplayName: 'Sam from Slack',
      callerEmail: 'forged@evil.example',
    });
    expect(reply.status).to.equal(200);
    await reply.text();
    expect(http.identify.calledOnce).to.equal(true);
    const [accepted] = logged(http.lines, 'admission.accepted');
    expect(accepted).to.include({ orgId: ORG, actorId: USER, agentKey: 'agent-1', via: 'service', surface: 'slack' });
    const run = (await http.store.get(ORG, String(accepted!['runId'])))!;
    expect(run.input?.caller).to.deep.equal({ email: SLACK_EMAIL, displayName: 'Sam from Slack' });
    expect(http.legacy.called).to.equal(false);
  });

  it('C-20: a Slack turn streams the slack audience: the timeline and streamed answer, no tool argument, and the log says so', async () => {
    http = await start({ conversation, scenario: 'agent_quick_tool_call' });
    const reply = await http.post('/agents/agent-1/conversations/internal/stream', 'service', { query: 'How much paid leave do I get?', chatMode: 'quick' });
    const body = await reply.text();
    for (const shown of ['TOOL_CALL_START', 'TOOL_CALL_RESULT', 'TEXT_MESSAGE_CONTENT', '"status":"running_tool"']) {
      expect(body, shown).to.contain(shown);
    }
    for (const hidden of ['TOOL_CALL_ARGS', 'paid leave\\"', 'argsSummary', 'resultSummary', 'goal_description']) {
      expect(body, hidden).to.not.contain(hidden);
    }
    for (const event of ['projection.subscribe', 'projection.finished', 'projection.unsubscribe']) {
      const [line] = logged(http.lines, event);
      expect(line, event).to.include({ audience: 'slack' });
      expect(JSON.stringify(line), `${event} carries no content`).to.not.contain('paid leave');
    }
  });

  it('SF-8: a plain chat in Universal Agent Mode (what Slack\'s plain bots send) stays on legacy with the rollout\'s reason', async () => {
    http = await start();
    const mcpAgentMode = await http.post('/conversations/stream', 'pat', { query: 'q', chatMode: 'agent' }, MCP);
    expect(await mcpAgentMode.text()).to.equal('legacy');
    expect(logged(http.lines, 'admission.legacy')[0]).to.include({ surface: 'mcp', reason: 'chat_mode_not_supported' });
  });

  it('SF-8: a body the durable route cannot serve as sent stays on legacy', async () => {
    http = await start();
    for (const extra of [
      { previousConversations: [{ role: 'user_query', content: 'earlier' }] },
      { attachments: [{ recordName: 'no id' }] },
    ]) {
      const reply = await http.post('/conversations/stream', 'pat', { query: 'q', chatMode: 'internal_search', ...extra }, MCP);
      expect(await reply.text(), JSON.stringify(extra)).to.equal('legacy');
    }
    expect(http.submit.called).to.equal(false);
  });

  it('LAT1: a project the body names keeps the new conversation on legacy, decided by routing; nothing is written', async () => {
    http = await start();
    const reply = await http.post('/conversations/stream', 'pat', { query: 'q', chatMode: 'internal_search', projectId: new Types.ObjectId().toHexString() }, MCP);
    expect(await reply.text()).to.equal('legacy');
    expect(logged(http.lines, 'admission.legacy')[0]).to.include({ surface: 'mcp', reason: 'project_scope' });
    expect(logged(http.lines, 'admission.accepted')).to.deep.equal([]);
  });

  it('ST-1/SF-8: a second MCP turn while the first runs joins it; the tool result says so', async () => {
    http = await start({ conversation });
    await (await http.post('/conversations/stream', 'pat', { query: 'first', chatMode: 'internal_search' }, MCP)).text();
    const [accepted] = logged(http.lines, 'admission.accepted');
    const sessionId = String(accepted!['sessionId']);
    const again = await http.post(`/conversations/${sessionId}/messages/stream`, 'pat', { query: 'SENTINEL-second-turn', chatMode: 'internal_search' }, MCP);
    expect(again.status).to.equal(200);
    const body = await again.text();
    const tool = await mcpToolResult(parser, body);
    expect(tool.terminal).to.equal('RUN_FINISHED');
    expect(tool.result).to.include({ conversationId: sessionId, status: 'Inprogress', answer: 'Added to the running task.' });
    const [queued] = logged(http.lines, 'input.queued');
    expect(queued).to.include({ surface: 'mcp', via: 'pat', route: 'chat', messageSeq: 1, sessionId, runId: accepted!['runId'] });
    expect(JSON.stringify(http.lines)).not.to.contain('SENTINEL-second-turn');
    expect(logged(http.lines, 'admission.accepted')).to.have.length(1);
    expect(http.legacy.called).to.equal(false);
    const run = (await http.store.get(ORG, String(accepted!['runId'])))!;
    expect(run.pendingInputs).to.deep.equal([1]);
  });

  it('ST-1: a second Slack message while the agent runs joins it; the bot posts the acknowledgement as its answer', async () => {
    http = await start({ conversation, scenario: 'agent_quick_tool_call' });
    await (await http.post('/agents/agent-1/conversations/internal/stream', 'service', { query: 'first', chatMode: 'quick' })).text();
    const [accepted] = logged(http.lines, 'admission.accepted');
    const sessionId = String(accepted!['sessionId']);
    const again = await http.post(`/agents/agent-1/conversations/internal/${sessionId}/messages/stream`, 'service', { query: 'and the 2025 one', chatMode: 'quick' });
    const writes = slackRendering(await again.text());
    expect(writes[writes.length - 1]).to.deep.equal({ final: { answer: 'Added to the running task.', citations: [] } });
    expect(logged(http.lines, 'input.queued')[0]).to.include({ surface: 'slack', via: 'service', messageSeq: 1 });
    expect(http.legacy.called).to.equal(false);
  });

  it('a hydration failure leaves the turn to the legacy handler, which answers it', async () => {
    http = await start({ identifyFails: true });
    const reply = await http.post('/agents/agent-1/conversations/internal/stream', 'service', { query: 'q', chatMode: 'quick' });
    expect(await reply.text()).to.equal('legacy');
    expect(http.submit.called).to.equal(false);
  });
});

const KB_1 = '0b1c2d3e-4f50-4a61-8b72-9c8d7e6f5a40';

describe('agent-harness: a surface body as a durable run input', () => {
  it('keeps what the run carries, drops what it serves anyway, mirrors legacy defaults', () => {
    expect(
      chatTurnInput(
        {
          query: 'q',
          filters: { apps: [], kb: [KB_1] },
          modelKey: 'm1',
          modelName: null,
          protocol: 'agui',
          callerDisplayName: 'Sam',
          previousConversations: [],
          attachments: [{ recordId: 'r1', recordName: 'a.pdf', size: 12 }],
        },
        'chat',
      ),
    ).to.deep.equal({
      query: 'q',
      recordIds: [],
      filters: { apps: [], kb: [KB_1] },
      attachments: [{ recordId: 'r1', recordName: 'a.pdf' }],
      model: { modelKey: 'm1', chatMode: 'quick' },
    });
    expect(chatTurnInput({ query: 'q' }, 'agent')?.model.chatMode).to.equal('auto');
    expect(chatTurnInput({ query: '' }, 'chat')).to.equal(null);
    expect(chatTurnInput('not an object', 'chat')).to.equal(null);
    // SYNCMAIN2: an edited question (main #4424) is today's loop's; a plain regenerate re-asks the stored question.
    expect(chatTurnInput({ query: 'the question, edited' }, 'chat', 'regenerate')).to.equal(null);
    expect(chatTurnInput({ attachments: [] }, 'chat', 'regenerate')).to.equal(null);
    expect(chatTurnInput({ chatMode: 'quick' }, 'chat', 'regenerate')?.query).to.equal('');
    // SYNC3: the answer to today's loop's approval card resumes that loop's parked turn; a durable run asks through its own interaction.
    expect(
      chatTurnInput({ query: 'Tool approval: Allow', toolApproval: { approvalId: 'a1', decision: 'allow' } }, 'chat'),
    ).to.equal(null);
  });
});

describe('agent-harness: ChatTurnService', () => {
  const durable = { route: 'durable', admission: { run: { runId: 'run-1', sessionId: 'ses-1' }, created: true } };
  const request = {
    orgId: 'org-1',
    actorId: 'user-1',
    surface: 'mcp' as const,
    idempotencyKey: 'surface:k',
    deadlineMs: 60_000,
    input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'internal_search' } },
  };

  for (const [surface, session, expected] of [
    ['mcp', 'owner', 'external'],
    ['slack', 'owner', 'slack'],
    ['mcp', 'shared', 'shared'],
    ['slack', 'shared', 'shared'],
  ] as const) {
    it(`a ${surface} turn on a session the caller watches as ${session} is streamed to the ${expected} audience`, async () => {
      const open = sinon.stub().returns({ close: () => undefined, done: Promise.resolve() });
      const service = new ChatTurnService({
        admissions: { submit: sinon.stub().resolves(durable) } as never,
        runs: { admittedStreamAccess: sinon.stub().resolves({ run: { runId: 'run-1' }, access: { audience: session, title: 'T' } }) } as never,
        streams: { open } as never,
      });
      const start = await service.start({ ...request, surface });
      expect(start.route).to.equal('durable');
      if (start.route === 'durable') start.open({} as never);
      expect(open.firstCall.args[0]).to.include({ audience: expected, title: 'T' });
    });
  }
});
