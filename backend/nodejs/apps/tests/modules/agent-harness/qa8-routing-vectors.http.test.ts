/**
 * QA-8 QA8-8 (RTE-19, LEG-07): the routing vectors over the existing chat
 * routes, for every client kind that sends a turn over HTTP (the web app, a
 * PAT, an OAuth app, MCP, Slack). Each turn is one request: a legacy route is
 * answered by the legacy handler in that request with nothing written, a
 * durable one is streamed on the same response.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { Types } from 'mongoose';
import { persistedRun } from '../../contracts/support/surfaces';
import { logged, sseFrames, startChatTurnHarness } from './support/chat-turn-harness';
import {
  inputOf,
  isValidInput,
  rolloutRoutingFromWorld,
  ROUTING_VECTORS,
  RoutingInput,
  routingWorld,
  tableAnswer,
} from './support/routing-vectors';

/** How each client kind reaches the chat routes in the in-process app. */
const CLIENTS: Record<string, { identity: string; headers?: Record<string, string>; slack?: true }> = {
  web: { identity: 'session' },
  api_pat: { identity: 'pat' },
  api_oauth: { identity: 'oauth' },
  mcp: { identity: 'pat', headers: { 'x-pipeshub-surface': 'mcp' } },
  slack: { identity: 'service', slack: true },
};

const pathOf = (input: RoutingInput, sessionId: string | undefined): string => {
  const agent = input.kind === 'agent' ? `/agents/agent-1${CLIENTS[input.surface]?.slack === true ? '/conversations/internal' : '/conversations'}` : '/conversations';
  return sessionId === undefined ? `${agent}/stream` : `${agent}/${sessionId}/messages/stream`;
};

/**
 * The HTTP cases: every named vector without an engine, under each client
 * kind its constraints allow. A durable turn into an existing conversation
 * needs that conversation's run history, which the ExecutionStore suites own.
 */
const cases = ROUTING_VECTORS.vectors.flatMap((vector) => {
  const base = inputOf(vector.input);
  if (base.surface === 'schedule' || base.engine !== 'none') return [];
  return Object.keys(CLIENTS)
    .map((surface) => ({ id: vector.id, input: { ...base, surface } }))
    .filter(({ input }) => isValidInput(input))
    .map((c) => ({ ...c, expected: tableAnswer(c.input)?.answer }))
    .filter(({ input, expected }) => input.conversation === 'new' || expected?.route === 'legacy');
});

describe('QA-8 QA8-8: routing vectors over the existing chat routes, one request per turn', () => {
  let conversation: Record<string, unknown>;

  before(async () => {
    conversation = (await persistedRun('chat_answer_with_citations')).conversation;
    sinon.restore();
  });

  afterEach(() => sinon.restore());

  for (const { id, input, expected } of cases) {
    it(`${id} ${input.surface} ${input.kind} ${input.chatMode}: ${expected?.route} ${expected?.reason ?? ''}`, async () => {
      const http = await startChatTurnHarness({
        conversation,
        publicSurface: 'web',
        scenario: input.kind === 'agent' ? 'agent_quick_tool_call' : 'chat_answer_with_citations',
        routing: rolloutRoutingFromWorld(routingWorld(input)),
      });
      try {
        const client = CLIENTS[input.surface] as (typeof CLIENTS)[string];
        const sessionId = input.conversation === 'new' ? undefined : new Types.ObjectId().toHexString();
        const body = {
          query: 'q',
          chatMode: input.chatMode,
          ...(input.project === 'request' ? { projectId: new Types.ObjectId().toHexString() } : {}),
          ...(input.harnessRequest !== 'none' ? { harness: input.harnessRequest } : {}),
        };
        const reply = await http.post(pathOf(input, sessionId), client.identity, body, client.headers);
        const text = await reply.text();
        expect(http.submit.callCount, 'admission asked once').to.equal(1);
        if (expected?.route === 'legacy') {
          expect(text).to.equal('legacy');
          expect(http.legacy.callCount).to.equal(1);
          expect(logged(http.lines, 'admission.legacy')[0]).to.include({ reason: expected.reason });
          expect(logged(http.lines, 'admission.accepted')).to.deep.equal([]);
        } else {
          expect(reply.headers.get('content-type')).to.equal('text/event-stream');
          expect(sseFrames(text).pop()?.data['type']).to.equal('RUN_FINISHED');
          expect(http.legacy.called, 'the legacy handler never ran').to.equal(false);
          expect(logged(http.lines, 'admission.accepted')[0]).to.include({
            surface: input.surface === 'slack' || input.surface === 'mcp' ? input.surface : 'web',
          });
        }
      } finally {
        await http.close();
      }
    });
  }

  it('covers every client kind with a legacy and a durable turn', () => {
    for (const surface of Object.keys(CLIENTS)) {
      const routes = new Set(cases.filter((c) => c.input.surface === surface).map((c) => c.expected?.route));
      expect([...routes].sort(), surface).to.deep.equal(['durable', 'legacy']);
    }
  });
});
