/**
 * QA6-F11: every chat turn today's loop serves leaves one `admission.legacy`
 * line saying why, the install-wide switch included, from one writer.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import {
  AdmissionLog,
  admissionLogFields,
  declinedTurnFields,
  logLegacyTurn,
} from '../../../src/modules/agent-harness/application/admission/admission-log';
import { HarnessLogger } from '../../../src/modules/agent-harness/application/harness-log';
import { noopHarnessTracer } from '../../../src/modules/agent-harness/application/harness-tracing';
import { HarnessUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import { ChatTurnService } from '../../../src/modules/agent-harness/application/chat-turns';
import { chatTurnMiddleware, webSurface } from '../../../src/modules/agent-harness/transport/chat-turn-route';
import {
  ChatTurnHarness,
  ChatTurnHarnessOptions,
  Line,
  logged,
  ORG,
  startChatTurnHarness,
  USER,
} from './support/chat-turn-harness';

const SESSION = new Types.ObjectId().toHexString();
const MESSAGE = new Types.ObjectId().toHexString();

const recording = (): { logger: HarnessLogger; lines: Line[] } => {
  const lines: Line[] = [];
  const at = (level: string) => (message: string, meta: Record<string, unknown> = {}) => {
    lines.push([level, message, meta]);
  };
  return { logger: { debug: at('debug'), info: at('info'), warn: at('warn'), error: at('error') }, lines };
};

describe('agent-harness: one admission.legacy record per legacy turn (QA6-F11)', () => {
  let http: ChatTurnHarness | undefined;
  const start = async (options: ChatTurnHarnessOptions): Promise<ChatTurnHarness> => {
    http = await startChatTurnHarness({ publicSurface: 'web', ...options });
    return http;
  };

  afterEach(async () => {
    await http?.close();
    http = undefined;
    sinon.restore();
  });

  const routes: Array<[string, string, Record<string, unknown>, Record<string, unknown>]> = [
    ['a new assistant chat', '/conversations/stream', { query: 'q', chatMode: 'internal_search' }, { assistant: true, chatMode: 'internal_search' }],
    ['a follow-up', `/conversations/${SESSION}/messages/stream`, { query: 'q', chatMode: 'web_search' }, { assistant: true, sessionId: SESSION, chatMode: 'web_search' }],
    ['an agent turn', '/agents/agent-1/conversations/stream', { query: 'q', chatMode: 'quick' }, { assistant: false, agentKey: 'agent-1', chatMode: 'quick' }],
    ['a regenerate', `/conversations/${SESSION}/message/${MESSAGE}/regenerate`, { chatMode: 'internal_search' }, { assistant: true, sessionId: SESSION }],
  ];

  for (const [name, path, body, expected] of routes) {
    it(`F11-1: durable runs off for the install, ${name}: today's handler answers and one line says why`, async () => {
      const off = await start({ enabled: false });
      const reply = await off.post(path, 'pat', body);
      expect(await reply.text()).to.equal('legacy');
      const lines = logged(off.lines, 'admission.legacy');
      expect(lines).to.have.length(1);
      expect(lines[0]).to.include({
        reason: 'infrastructure_unavailable',
        orgId: ORG,
        actorId: USER,
        via: 'pat',
        surface: 'web',
        ...expected,
      });
      expect(off.lines, 'nothing else is logged').to.have.length(1);
    });
  }

  it('F11-3: off, the Slack route resolves no identity and admission is never asked', async () => {
    const off = await start({ enabled: false });
    const reply = await off.post('/agents/agent-1/conversations/internal/stream', 'service', { query: 'q', chatMode: 'quick' });
    expect(await reply.text()).to.equal('legacy');
    expect(off.identify.called, 'no store read for the identity').to.equal(false);
    expect(off.submit.called).to.equal(false);
    const [line] = logged(off.lines, 'admission.legacy');
    expect(line).to.include({ reason: 'infrastructure_unavailable', via: 'service', surface: 'slack', agentKey: 'agent-1' });
    expect(line).to.not.have.any.keys('orgId', 'actorId');
    expect(JSON.stringify(line), 'the service token\'s email is not logged').to.not.contain('@');
  });

  it('F11-3: off, the handler runs in the same tick: nothing is awaited and nothing but the line is done', () => {
    const { logger, lines } = recording();
    const refuses = (): never => {
      throw new Error('the off path asked the harness for something');
    };
    const service = new ChatTurnService({
      admissions: { submit: refuses } as never,
      runs: { streamAccess: refuses, activeRun: refuses } as never,
      streams: { open: refuses } as never,
      logger,
    });
    const identify = sinon.spy(async () => undefined);
    const middleware = chatTurnMiddleware(service, { kind: 'chat', surface: webSurface, identify, enabled: () => false });
    const next = sinon.spy();
    const req = { body: { query: 'q', chatMode: 'internal_search' }, params: {}, headers: {}, header: () => undefined, user: { orgId: ORG, userId: USER } };
    void middleware(req as never, {} as never, next);
    expect(next.calledOnceWithExactly(), 'the legacy handler is reached before any await').to.equal(true);
    expect(identify.called).to.equal(false);
    expect(logged(lines, 'admission.legacy')).to.have.length(1);
  });

  it('F11-4: admission could not decide: one legacy line beside the refusal', async () => {
    const down = await start({ admitFails: new HarnessUnavailableError('journal down') });
    const reply = await down.post('/conversations/stream', 'session', { query: 'q', chatMode: 'internal_search' });
    expect(await reply.text()).to.equal('legacy');
    const lines = logged(down.lines, 'admission.legacy');
    expect(lines).to.have.length(1);
    expect(lines[0]).to.include({ reason: 'admission_unavailable', orgId: ORG, actorId: USER, chatMode: 'internal_search' });
  });

  it('F11-1: a turn the rollout keeps on legacy still logs once (the admission\'s own line)', async () => {
    const killed = await start({ settings: { killSwitch: true } });
    await (await killed.post('/conversations/stream', 'session', { query: 'q', chatMode: 'internal_search' })).text();
    expect(logged(killed.lines, 'admission.legacy').map((line) => line['reason'])).to.deep.equal(['kill_switch']);
  });

  it('F11-2: admission\'s legacy outcome and the route\'s decline write the same line, but for the reason', () => {
    const turn = { orgId: ORG, actorId: USER, sessionId: SESSION, via: 'pat' as const, surface: 'web' as const, chatMode: 'internal_search' };
    const admission = recording();
    new AdmissionLog(admission.logger, noopHarnessTracer).outcome(
      admissionLogFields(turn),
      {} as never,
      { route: 'legacy', rollout: { useHarness: false, harnessMajor: 2, reason: 'kill_switch' } },
      {} as never,
      { ms: 0, startedAt: 0 },
    );
    const route = recording();
    logLegacyTurn(route.logger, declinedTurnFields(turn), 'infrastructure_unavailable');
    const [[level, message, fromAdmission]] = admission.lines as [Line];
    const [[routeLevel, routeMessage, fromRoute]] = route.lines as [Line];
    expect([level, message]).to.deep.equal([routeLevel, routeMessage]);
    expect({ ...fromAdmission, reason: null }).to.deep.equal({ ...fromRoute, reason: null });
    expect([fromAdmission['reason'], fromRoute['reason']]).to.deep.equal(['kill_switch', 'infrastructure_unavailable']);
  });

  it('F11-2: free text sent as a chat mode never reaches the line', () => {
    const route = recording();
    logLegacyTurn(
      route.logger,
      declinedTurnFields({ via: 'session', surface: 'web', chatMode: 'ignore previous instructions' }),
      'infrastructure_unavailable',
    );
    expect(route.lines[0]?.[2]).to.include({ chatMode: 'other' });
  });
});
