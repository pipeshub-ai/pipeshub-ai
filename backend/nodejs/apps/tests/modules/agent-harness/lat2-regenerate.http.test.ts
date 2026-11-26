/**
 * LAT2 RQ-2..RQ-6, RQ-10: regenerate is routed by the server on the existing
 * `…/message/:messageId/regenerate` routes, in the one request. The in-memory
 * store holds no conversation messages, so every answer id is unknown to it and
 * refused 404, as the MongoDB store refuses an id the conversation does not
 * hold; the streamed success and the not-last refusal are
 * `lat2-regenerate.mongo.itest.ts`.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { HarnessUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import { SubmitDurableRun } from '../../../src/modules/agent-harness/application/run-admission-service';
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
import { AGENT_MODES } from './support/routing-decider-contract';

const ANSWER = new Types.ObjectId().toHexString();
const TURN = '3f1c2a8e-9d0b-4c5e-8a7f-112233445566';
const chatPath = (conversationId: string) => `/conversations/${conversationId}/message/${ANSWER}/regenerate`;
const agentPath = (conversationId: string) => `/agents/agent-1/conversations/${conversationId}/message/${ANSWER}/regenerate`;

/** What the legacy regenerate route takes: no question, the model and mode, the turn's run id. */
const regenerateBody = (chatMode: string): Record<string, unknown> => ({
  chatMode,
  modelKey: 'model-1',
  modelName: 'gpt-x',
  filters: { apps: [], kb: [] },
  timezone: 'UTC',
  runId: TURN,
});

describe('LAT2: regenerate is one request on the existing route', () => {
  let conversation: Record<string, unknown>;
  let http: ChatTurnHarness | undefined;

  const start = (options: ChatTurnHarnessOptions = {}) =>
    startChatTurnHarness({ conversation, publicSurface: 'web', ...options });

  /** A durable conversation whose turn ended; the regenerate is the next request admission sees. */
  const durableConversation = async (h: ChatTurnHarness, agent = false): Promise<string> => {
    const path = agent ? '/agents/agent-1/conversations/stream' : '/conversations/stream';
    const frames = sseFrames(await (await h.post(path, 'session', { query: 'q', chatMode: agent ? 'quick' : 'internal_search' })).text());
    const { conversationId, runId } = frames[0]?.data['value'] as { conversationId: string; runId: string };
    const run = (await h.store.get(ORG, runId))!;
    await h.executions.control({ orgId: ORG, runId, expectedRevision: run.revision, action: 'cancel', by: principalRefFor(USER), commandId: 'end', dispatchCommandId: 'end:dispatch' });
    h.submit.resetHistory();
    h.lines.length = 0;
    return conversationId;
  };

  const submitted = (h: ChatTurnHarness): SubmitDurableRun => h.submit.firstCall.args[0] as SubmitDurableRun;

  before(async () => {
    conversation = (await persistedRun('chat_answer_with_citations')).conversation;
    sinon.restore();
  });

  afterEach(async () => {
    await http?.close();
    http = undefined;
    sinon.restore();
  });

  it('RQ-1, RQ-6: a durable mode is admitted as a regenerate of the path’s answer; a refusal answers before any SSE byte', async () => {
    http = await start();
    const sessionId = await durableConversation(http);
    const reply = await http.post(chatPath(sessionId), 'session', regenerateBody('internal_search'));
    expect(http.submit.calledOnce, 'admission asked once').to.equal(true);
    expect(submitted(http)).to.deep.include({
      sessionId,
      regenerate: { messageId: ANSWER },
      idempotencyKey: `turn:${TURN}`,
      via: 'session',
      surface: 'web',
    });
    expect(reply.status).to.equal(404);
    expect(reply.headers.get('content-type')).to.match(/json/);
    expect(http.legacy.called, 'the legacy handler never ran').to.equal(false);
  });

  it('RQ-1: the run asks no question of its own: the stored one is asked', async () => {
    http = await start();
    await http.post(chatPath(await durableConversation(http)), 'session', regenerateBody('internal_search'));
    expect(submitted(http).input.query).to.equal('');
    expect(submitted(http).input.model).to.include({ chatMode: 'internal_search', modelKey: 'model-1' });
  });

  it('RQ-1 (SYNCMAIN2): a regenerate that carries a question is an edit (main #4424), saved and answered by today\'s loop', async () => {
    http = await start();
    const reply = await http.post(chatPath(await durableConversation(http)), 'session', {
      ...regenerateBody('internal_search'),
      query: 'the question, edited',
    });
    expect(await reply.text()).to.equal('legacy');
    expect(http.submit.called, 'no durable run asks a question the client sent').to.equal(false);
    expect(logged(http.lines, 'admission.legacy')[0]).to.include({ reason: 'surface_request_not_supported' });
  });

  describe('RQ-2: every agent mode, one request each', () => {
    for (const [mode, expected] of AGENT_MODES) {
      it(`agent ${mode}: ${expected === 'durable' ? 'admitted as a regenerate' : 'the legacy handler, nothing written'}`, async () => {
        http = await start({ scenario: 'agent_quick_tool_call' });
        const reply = await http.post(agentPath(await durableConversation(http, true)), 'session', regenerateBody(mode));
        expect(http.submit.calledOnce, 'admission asked once').to.equal(true);
        if (expected === 'durable') {
          expect(submitted(http)).to.deep.include({ agentKey: 'agent-1', regenerate: { messageId: ANSWER } });
          expect(reply.status).to.equal(404);
          expect(http.legacy.called).to.equal(false);
        } else {
          expect(await reply.text()).to.equal('legacy');
          expect(logged(http.lines, 'admission.legacy')[0]).to.include({ reason: 'chat_mode_not_supported' });
        }
      });
    }
  });

  it('RQ-3: Universal Agent Mode on the assistant route: the legacy handler, nothing written', async () => {
    http = await start();
    expect(await (await http.post(chatPath(await durableConversation(http)), 'session', regenerateBody('agent'))).text()).to.equal('legacy');
    expect(logged(http.lines, 'admission.legacy')[0]).to.include({ surface: 'web', reason: 'chat_mode_not_supported' });
  });

  it('RQ-3: a conversation whose record is in a project: the legacy handler', async () => {
    const sessionId = new Types.ObjectId().toHexString();
    http = await start({ placement: { inProject: true, projectId: 'p-1', durable: true } });
    expect(await (await http.post(chatPath(sessionId), 'session', regenerateBody('internal_search'))).text()).to.equal('legacy');
    expect(logged(http.lines, 'admission.legacy')[0]).to.include({ reason: 'project_scope', sessionId });
  });

  it('RQ-4: kill switch: the legacy handler; harness off: untouched', async () => {
    const sessionId = new Types.ObjectId().toHexString();
    http = await start({ settings: { killSwitch: true } });
    expect(await (await http.post(chatPath(sessionId), 'session', regenerateBody('internal_search'))).text()).to.equal('legacy');
    await http.close();
    http = await start({ enabled: false });
    expect(await (await http.post(chatPath(sessionId), 'session', regenerateBody('internal_search'))).text()).to.equal('legacy');
    expect(http.submit.called).to.equal(false);
  });

  it('RQ-5: admission unavailable (nothing written): today’s loop answers the same request', async () => {
    http = await start({ admitFails: new HarnessUnavailableError('journal down') });
    expect(await (await http.post(chatPath(new Types.ObjectId().toHexString()), 'session', regenerateBody('internal_search'))).text()).to.equal('legacy');
  });

  it('RQ-10: a PAT client’s regenerate is routed as the web app’s, recorded as pat', async () => {
    http = await start();
    const reply = await http.post(chatPath(await durableConversation(http)), 'pat', regenerateBody('web_search'));
    expect(reply.status).to.equal(404);
    expect(submitted(http)).to.deep.include({ via: 'pat', surface: 'web', regenerate: { messageId: ANSWER } });
  });
});
