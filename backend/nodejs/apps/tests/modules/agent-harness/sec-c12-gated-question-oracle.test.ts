/**
 * SEC-C-12: whether a conversation holds a gated (dry-run) question is told only to who may answer it there: its owner
 * or a collaborator (OWD-19). Anyone else, a read-only member included, gets exactly what a conversation that does not
 * exist gets, on every path that hands a turn to today's loop (harness off, a rollout that keeps the org there,
 * admission unavailable), and the gated-question store is not even read for them.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { HarnessUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import type { SessionRole } from '../../../src/modules/agent-harness/domain/session-roles';
import type { SessionExecutionScope } from '../../../src/modules/agent-harness/ports/chat-session-repository';
import { SURFACE_HEADER } from '../../../src/modules/agent-harness/transport/surface-header';
import { persistedRun } from '../../contracts/support/surfaces';
import {
  ChatTurnHarness as Harness,
  ChatTurnHarnessOptions,
  OTHER_USER,
  startChatTurnHarness,
  USER,
} from './support/chat-turn-harness';

const MCP = { [SURFACE_HEADER]: 'mcp' };
const ANSWER = { query: 'User selections:\n1. "Which region?" → EU', chatMode: 'internal_search' };
const ASKED = { runId: 'run-asked', gates: { dryRun: true as const } };
const GATED = 'c-1';

/** Every path a turn reaches today's loop by, where the gated-question check runs. */
const PATHS: Record<string, ChatTurnHarnessOptions> = {
  'harness off for the workspace': { enabled: false },
  'rollout keeps the org on today’s loop': { settings: { orgEnabled: false } },
  'admission unavailable': { admitFails: new HarnessUnavailableError('down') },
};

/** The conversation `GATED` is USER's; OTHER_USER holds `other` in it (null: no participant). */
const rolesWith = (other: SessionRole | null) => ({
  roleOf: async (scope: SessionExecutionScope): Promise<SessionRole | null> => {
    if (scope.sessionId !== GATED) return null;
    if (scope.actorId === USER) return 'owner';
    return scope.actorId === OTHER_USER ? other : null;
  },
});

describe('SEC-C-12: a gated question is told only to who may answer it', () => {
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

  const start = async (path: ChatTurnHarnessOptions, other: SessionRole | null) => {
    http = await startChatTurnHarness({ conversation, ...path, roles: rolesWith(other) });
    const read = sinon
      .stub(http.store, 'answeredRun')
      .callsFake(async (_orgId: string, sessionId: string) => (sessionId === GATED ? ASKED : null));
    return { harness: http, read };
  };
  const send = async (harness: Harness, conversationId: string, identity: string) => {
    const reply = await harness.post(`/conversations/${conversationId}/messages/stream`, identity, ANSWER, MCP);
    return { status: reply.status, body: await reply.text() };
  };

  for (const [name, path] of Object.entries(PATHS)) {
    describe(name, () => {
      it("a stranger's message to the gated conversation answers as one to a conversation that does not exist", async () => {
        const { harness } = await start(path, null);
        const stranger = await send(harness, GATED, 'otherUser');
        const missing = await send(harness, '0123456789abcdef01234567', 'otherUser');
        expect(stranger).to.deep.equal(missing);
        expect(stranger.body).to.not.contain('GATES_NEED_RUNTIME');
        expect(harness.legacy.callCount, 'both reach today’s loop, which answers not found').to.equal(2);
      });

      it('a read-only member is told nothing either: they may not answer here', async () => {
        const { harness } = await start(path, 'viewer');
        const viewer = await send(harness, GATED, 'otherUser');
        const missing = await send(harness, '0123456789abcdef01234567', 'otherUser');
        expect(viewer).to.deep.equal(missing);
      });

      it('the owner, and a collaborator, are still refused with GATES_NEED_RUNTIME (never served ungated)', async () => {
        const { harness } = await start(path, 'collaborator');
        for (const identity of ['session', 'otherUser']) {
          const reply = await send(harness, GATED, identity);
          expect(reply.status, identity).to.equal(409);
          expect(reply.body, identity).to.contain('GATES_NEED_RUNTIME');
        }
        expect(harness.legacy.called).to.equal(false);
      });
    });
  }

  it('reads no question for a caller who may not answer, so both cases take the same path', async () => {
    const { harness, read } = await start(PATHS['harness off for the workspace']!, null);
    await send(harness, GATED, 'otherUser');
    await send(harness, '0123456789abcdef01234567', 'otherUser');
    expect(read.called, 'the gated-question store was read for a stranger').to.equal(false);
  });

  it('a role read that fails is refused as unavailable (fail closed), for the owner as for anyone', async () => {
    http = await startChatTurnHarness({
      conversation, enabled: false, roles: { roleOf: async () => Promise.reject(new Error('down')) },
    });
    sinon.stub(http.store, 'answeredRun').resolves(ASKED);
    for (const identity of ['session', 'otherUser']) {
      expect((await send(http, GATED, identity)).status, identity).to.equal(503);
    }
    expect(http.legacy.called).to.equal(false);
  });
});
