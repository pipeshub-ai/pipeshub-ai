/**
 * LAT1 OR-1..OR-8, OR-12: every chat turn, from the web app or an API/PAT
 * client, is admitted and streamed by the existing chat routes in one
 * request, or served by the legacy handler in that same request.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { HarnessQuotaExceededError, HarnessRunConfigUnavailableError, HarnessUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import { RunProjector } from '../../../src/modules/agent-harness/projection/run-projector';
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
import { AGENT_MODES, type RoutingWorld } from './support/routing-decider-contract';
import { ResolvedRoutingDecider } from '../../../src/modules/agent-harness/application/resolved-routing';
import { InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';
import { controlPlaneOfWorld, factsOfWorld } from './support/routing-vectors';

describe('LAT1: one request on the existing chat routes', () => {
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

  it('OR-1: a web turn in internal_search is admitted and streamed on the same response, conversation_created first with its run', async () => {
    http = await start();
    const reply = await http.post('/conversations/stream', 'session', { query: 'What is the refund policy?', chatMode: 'internal_search', protocol: 'agui' });
    expect(reply.status).to.equal(200);
    expect(reply.headers.get('content-type')).to.equal('text/event-stream');
    const frames = sseFrames(await reply.text());
    const [accepted] = logged(http.lines, 'admission.accepted');
    expect(accepted).to.include({ via: 'session', surface: 'web', chatMode: 'internal_search', assistant: true });
    expect(frames[0]?.data).to.deep.include({ type: 'CUSTOM', name: 'conversation_created' });
    expect(frames[0]?.data['value']).to.deep.include({ conversationId: accepted!['sessionId'], runId: accepted!['runId'] });
    expect(frames.every((frame) => frame.id !== undefined), 'every frame resumable').to.equal(true);
    expect(frames[frames.length - 1]?.data['type']).to.equal('RUN_FINISHED');
    expect(http.legacy.called, 'the legacy handler never ran').to.equal(false);
    expect(http.submit.calledOnce).to.equal(true);
  });

  describe('OR-2, OR-3: every agent mode, one request each', () => {
    for (const [mode, expected] of AGENT_MODES) {
      it(`agent ${mode}: ${expected === 'durable' ? 'streamed durably' : 'the legacy handler, nothing written'}`, async () => {
        http = await start({ scenario: 'agent_quick_tool_call' });
        const reply = await http.post('/agents/agent-1/conversations/stream', 'session', { query: 'q', chatMode: mode });
        const body = await reply.text();
        expect(http.submit.calledOnce, 'admission asked once').to.equal(true);
        if (expected === 'durable') {
          expect(sseFrames(body).pop()?.data['type']).to.equal('RUN_FINISHED');
          expect(http.legacy.called).to.equal(false);
          expect(logged(http.lines, 'admission.accepted')[0]).to.include({ chatMode: mode.toLowerCase(), agentKey: 'agent-1' });
        } else {
          expect(body).to.equal('legacy');
          expect(logged(http.lines, 'admission.legacy')[0]).to.include({ reason: 'chat_mode_not_supported' });
          expect(logged(http.lines, 'admission.accepted')).to.deep.equal([]);
        }
      });
    }

    it('OR-3: Universal Agent Mode on the assistant route: the legacy handler, nothing written', async () => {
      http = await start();
      expect(await (await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'agent' })).text()).to.equal('legacy');
      expect(logged(http.lines, 'admission.legacy')[0]).to.include({ surface: 'web', reason: 'chat_mode_not_supported' });
    });

    it('OR-3: a conversation whose record is in a project: the legacy handler, whatever the body says', async () => {
      const sessionId = new Types.ObjectId().toHexString();
      http = await start({ placement: { inProject: true, projectId: 'p-1', durable: false } });
      const reply = await http.post(`/conversations/${sessionId}/messages/stream`, 'session', { query: 'q', chatMode: 'internal_search' });
      expect(await reply.text()).to.equal('legacy');
      expect(logged(http.lines, 'admission.legacy')[0]).to.include({ reason: 'project_scope', sessionId });
    });

    it('OR-3: a new conversation the body creates in a project: the legacy handler', async () => {
      http = await start();
      const reply = await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'internal_search', projectId: new Types.ObjectId().toHexString() });
      expect(await reply.text()).to.equal('legacy');
      expect(logged(http.lines, 'admission.legacy')[0]).to.include({ reason: 'project_scope' });
    });
  });

  it('OR-4: a PAT client and an OAuth app get the same routing, recorded by how they authenticated', async () => {
    http = await start();
    for (const [identity, via] of [['pat', 'pat'], ['oauth', 'oauth_app']] as const) {
      const reply = await http.post('/conversations/stream', identity, { query: 'q', chatMode: 'web_search' });
      const frames = sseFrames(await reply.text());
      expect(frames[0]?.data['value'], identity).to.have.property('runId');
      expect(logged(http.lines, 'admission.accepted').pop()).to.include({ via, surface: 'web', orgId: ORG, actorId: USER });
    }
    expect(logged(http.lines, 'projection.subscribe').map((line) => line['audience'])).to.deep.equal(['owner', 'owner']);
  });

  it('OR-5: a follow-up on a durable conversation whose run ended is admitted into it as a new run', async () => {
    http = await start();
    await (await http.post('/conversations/stream', 'session', { query: 'first', chatMode: 'internal_search' })).text();
    const [first] = logged(http.lines, 'admission.accepted');
    const sessionId = String(first!['sessionId']);
    const firstRunId = String(first!['runId']);
    const queued = await http.store.get(ORG, firstRunId);
    expect(queued?.status).to.equal('queued');
    await http.store.control({
      commandId: 'end-1',
      dispatchCommandId: 'end-1-dispatch',
      orgId: ORG,
      runId: firstRunId,
      expectedRevision: queued!.revision,
      action: 'cancel',
    });
    expect((await http.store.get(ORG, firstRunId))?.status).to.equal('cancelled');
    const reply = await http.post(`/conversations/${sessionId}/messages/stream`, 'session', { query: 'second', chatMode: 'internal_search' });
    expect(reply.status).to.equal(200);
    await reply.text();
    const accepted = logged(http.lines, 'admission.accepted');
    expect(accepted.map((line) => line['sessionId'])).to.deep.equal([sessionId, sessionId]);
    expect(accepted[1]?.['runId']).to.be.a('string').and.not.equal(firstRunId);
    expect(logged(http.lines, 'input.queued'), 'not steered into the ended run').to.deep.equal([]);
  });

  it('OR-6: the same turn sent again with one Idempotency-Key streams the same run; a body runId names the turn too', async () => {
    http = await start();
    const send = (headers: Record<string, string>, extra: Record<string, unknown> = {}) =>
      http!.post('/conversations/stream', 'session', { query: 'q', chatMode: 'internal_search', ...extra }, headers).then(async (r) => sseFrames(await r.text()));
    const first = await send({ 'Idempotency-Key': 'turn-abc' });
    const again = await send({ 'Idempotency-Key': 'turn-abc' });
    expect(again[0]?.data['value']).to.deep.equal(first[0]?.data['value']);
    const runId = '3f1c2a8e-9d0b-4c5e-8a7f-112233445566';
    const byRun = await send({}, { runId });
    const byRunAgain = await send({}, { runId });
    expect(byRunAgain[0]?.data['value']).to.deep.equal(byRun[0]?.data['value']);
    expect(logged(http.lines, 'admission.accepted').map((line) => line['replayed'])).to.deep.equal([false, true, false, true]);
  });

  it('OR-6: a malformed Idempotency-Key is refused before anything is written', async () => {
    http = await start();
    const reply = await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'internal_search' }, { 'Idempotency-Key': 'has spaces/and slashes' });
    expect(reply.status).to.equal(400);
    expect(http.legacy.called).to.equal(false);
  });

  it('OR-7: a refusal answers its HTTP error and code before any SSE byte; nothing is written', async () => {
    http = await start({ admitFails: new HarnessQuotaExceededError('user', 'admission_rate', 60) });
    const reply = await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'internal_search' });
    expect(reply.status).to.equal(429);
    expect(reply.headers.get('content-type')).to.match(/json/);
    expect(JSON.stringify(await reply.json())).to.contain('QUOTA');
    expect(http.legacy.called).to.equal(false);
  });

  it('OR-8: admission unavailable (nothing written): today’s loop answers the same request', async () => {
    http = await start({ admitFails: new HarnessUnavailableError('journal down') });
    const reply = await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'internal_search' });
    expect(await reply.text()).to.equal('legacy');
  });

  it("CPD6-37: a pin that could not be made is answered 503 with its own code; today's loop never takes the turn", async () => {
    http = await start({ admitFails: new HarnessRunConfigUnavailableError() });
    const reply = await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'internal_search' });
    expect(reply.status).to.equal(503);
    expect(JSON.stringify(await reply.json())).to.contain('HTTP_AGENT_HARNESS_RUN_CONFIG_UNAVAILABLE');
    expect(http.legacy.called).to.equal(false);
  });

  describe('CPD10-7: routing by the resolved run configuration (CONTROL_PLANE_PIN=on)', () => {
    const world: RoutingWorld = { settings: { infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null }, workerLive: true };
    const resolved = (heads: InMemoryHeadRevisions) =>
      new ResolvedRoutingDecider({ pinner: controlPlaneOfWorld(world, heads).pinner, facts: factsOfWorld(world) });

    it('a route that cannot be resolved is answered 503 RUN_CONFIG_UNAVAILABLE; the legacy handler never runs and nothing is written', async () => {
      const heads = new InMemoryHeadRevisions();
      heads.unreadable = true;
      http = await start({ routing: resolved(heads) });
      const reply = await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'internal_search' });
      expect(reply.status).to.equal(503);
      expect(JSON.stringify(await reply.json())).to.contain('HTTP_AGENT_HARNESS_RUN_CONFIG_UNAVAILABLE');
      expect(http.legacy.called).to.equal(false);
      expect(logged(http.lines, 'admission.accepted')).to.deep.equal([]);
    });

    it('a route it resolves to today’s loop is still served by the legacy handler in the same request', async () => {
      http = await start({ routing: resolved(new InMemoryHeadRevisions()) });
      const reply = await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'image' });
      expect(await reply.text()).to.equal('legacy');
    });

    it('and a durable one is streamed on the same response', async () => {
      http = await start({ routing: resolved(new InMemoryHeadRevisions()) });
      const reply = await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'internal_search', protocol: 'agui' });
      expect(reply.status).to.equal(200);
      expect(sseFrames(await reply.text())[0]?.data).to.deep.include({ type: 'CUSTOM', name: 'conversation_created' });
      expect(http.legacy.called).to.equal(false);
    });
  });
});

describe('LAT1 OR-12: conversation_created names the run for its owner only', () => {
  for (const audience of ['owner', 'shared', 'slack', 'external'] as const) {
    it(audience, () => {
      const [first] = new RunProjector({ runId: 'run-1', sessionId: 's-1', conversationId: 's-1', goal: 'q', audience }).begin();
      const value = (first?.frame as { value?: Record<string, unknown> }).value;
      if (audience === 'owner') expect(value).to.include({ conversationId: 's-1', runId: 'run-1' });
      else expect(value).to.deep.equal({ conversationId: 's-1' });
    });
  }
});
