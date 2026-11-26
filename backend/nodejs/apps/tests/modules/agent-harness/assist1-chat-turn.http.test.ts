/**
 * ASSIST1 AS1-5 over real HTTP: the `harness` body field travels with an
 * assistant turn as a routing field. Node decides with the org setting; the
 * run input never carries the field; the agent routes ignore it.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { assistantModesWith } from '../../../src/modules/agent-harness/domain/rollout';
import { chatTurnInput, durableRequested } from '../../../src/modules/agent-harness/transport/chat-turn-route';
import { ChatTurnHarness, logged, ORG, startChatTurnHarness } from './support/chat-turn-harness';

const ORG_ON = { assistantModes: assistantModesWith(true) };

describe('ASSIST1: the assistant turn asks for the durable harness (AS1-5)', () => {
  let http: ChatTurnHarness | undefined;

  afterEach(async () => {
    await http?.close();
    http = undefined;
    sinon.restore();
  });

  it('org on and asked: the Agent-mode turn is admitted and streamed; the run input has no harness field', async () => {
    http = await startChatTurnHarness({ publicSurface: 'web', settings: ORG_ON });
    const reply = await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'agent', harness: 'durable' });
    expect(reply.status).to.equal(200);
    await reply.text();
    expect(http.legacy.called, 'the legacy handler never ran').to.equal(false);
    const [accepted] = logged(http.lines, 'admission.accepted');
    expect(accepted).to.include({ assistant: true, chatMode: 'agent', durableRequested: true });
    const run = (await http.store.get(ORG, String(accepted!['runId'])))!;
    expect(run.input?.model.chatMode).to.equal('agent');
    expect(run.input as unknown as Record<string, unknown>).not.to.have.property('harness');
  });

  for (const [name, settings, body] of [
    ['org on, not asked', ORG_ON, {}],
    ['org off, asked', {}, { harness: 'durable' }],
    ['org on, asked with another value', ORG_ON, { harness: 'legacy' }],
  ] as const) {
    it(`${name}: today's loop, chat_mode_not_supported, logged as today`, async () => {
      http = await startChatTurnHarness({ publicSurface: 'web', settings });
      const reply = await http.post('/conversations/stream', 'session', { query: 'q', chatMode: 'agent', ...body });
      expect(await reply.text()).to.equal('legacy');
      const [line] = logged(http.lines, 'admission.legacy');
      expect(line).to.include({ reason: 'chat_mode_not_supported', chatMode: 'agent' });
    });
  }

  it('an agent route serves a body with the field as before and never reads it', async () => {
    http = await startChatTurnHarness({ publicSurface: 'web' });
    const reply = await http.post('/agents/agent-1/conversations/stream', 'session', { query: 'q', chatMode: 'quick', harness: 'durable' });
    expect(reply.status).to.equal(200);
    await reply.text();
    const [accepted] = logged(http.lines, 'admission.accepted');
    expect(accepted).to.include({ agentKey: 'agent-1' });
    expect(accepted).not.to.have.property('durableRequested');
  });

  it('the field is a routing field: a body with it is still a run input, without it', () => {
    const body = { query: 'q', chatMode: 'agent', harness: 'durable' };
    const withField = chatTurnInput(body, 'chat');
    expect(withField).not.to.equal(null);
    expect(withField).to.deep.equal(chatTurnInput({ query: 'q', chatMode: 'agent' }, 'chat'));
    expect(durableRequested(body)).to.equal(true);
    expect(durableRequested({ harness: 'legacy' })).to.equal(false);
    expect(durableRequested(null)).to.equal(false);
  });
});
