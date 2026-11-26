/**
 * SS3 FK-12, FK-13: the fork routes. The caller and org come from the
 * credential, the body names only the request key (strict), and each refusal
 * maps to its status.
 */
import { expect } from 'chai';
import { forkConversation } from '../../../src/modules/enterprise_search/controller/session-fork.controller';
import {
  agentConversationForkSchema,
  conversationForkSchema,
} from '../../../src/modules/enterprise_search/validators/es_validators';
import { SessionForkService } from '../../../src/modules/agent-harness/application/session-fork-service';
import {
  HarnessConflictError,
  HarnessNotFoundError,
  HarnessUnavailableError,
} from '../../../src/modules/agent-harness/domain/errors';
import { WorkspaceError } from '../../../src/modules/agent-harness/domain/workspace';
import { inRequestOrg } from '../../helpers/request-org-context';

const CONVERSATION = '6a1b2c3d4e5f6a7b8c9d0e1f';
const OUTCOME = { sessionId: '6a1b2c3d4e5f6a7b8c9d0e20', fromSessionId: CONVERSATION, atSeq: 3, role: 'collaborator' as const, counts: { messages: 2, stubs: 2, citations: 1, citationsHidden: 0, artifacts: 0, artifactsHidden: 0, files: 0 } };

interface Answer {
  status?: number;
  body?: unknown;
  error?: unknown;
}

async function call(
  service: Partial<SessionForkService<never>> | undefined,
  params: Record<string, string> = { conversationId: CONVERSATION },
  user: Record<string, string> | undefined = { orgId: 'org-1', userId: 'bob' },
): Promise<{ answer: Answer; requests: unknown[] }> {
  const requests: unknown[] = [];
  const answer: Answer = {};
  const fake = service === undefined ? undefined : ({ fork: (request: unknown) => { requests.push(request); return service.fork!(request as never); } } as unknown as SessionForkService<never>);
  const res = {
    status(code: number) {
      answer.status = code;
      return this;
    },
    json(body: unknown) {
      answer.body = body;
      return this;
    },
  };
  await inRequestOrg(forkConversation(() => fake))(
    { params, body: { idempotencyKey: 'k-1' }, user, headers: { authorization: 'Bearer bob' }, context: { requestId: 'req-9' } } as never,
    res as never,
    (error: unknown) => {
      answer.error = error;
    },
  );
  return { answer, requests };
}

describe('SS3 fork routes (FK-13)', () => {
  it('201 with the new conversation; the caller, org and credential from the request, never the body', async () => {
    const { answer, requests } = await call({ fork: () => Promise.resolve({ ...OUTCOME, replayed: false }) });
    expect(answer).to.deep.equal({ status: 201, body: { conversationId: OUTCOME.sessionId, replayed: false } });
    expect(requests).to.deep.equal([
      { orgId: 'org-1', actorId: 'bob', sessionId: CONVERSATION, requestKey: 'k-1', authorization: 'Bearer bob', requestId: 'req-9' },
    ]);
  });

  it('200 when the key already made the copy; the agent route passes its agent', async () => {
    const { answer, requests } = await call({ fork: () => Promise.resolve({ ...OUTCOME, replayed: true }) }, { agentKey: 'agent-1', conversationId: CONVERSATION });
    expect(answer.status).to.equal(200);
    expect(requests[0]).to.deep.include({ agentKey: 'agent-1' });
  });

  it('FK-12: 503 without the harness infrastructure; 401 without a user', async () => {
    expect(((await call(undefined)).answer.error as { statusCode?: number }).statusCode).to.equal(503);
    expect(((await call({ fork: () => Promise.reject(new Error('unused')) }, undefined, {})).answer.error as { statusCode?: number }).statusCode).to.equal(401);
  });

  for (const [name, error, status, code] of [
    ['not a participant', new HarnessNotFoundError(), 404, undefined],
    ['a run is active', new HarnessConflictError('RUN_ACTIVE', 'running'), 409, 'HTTP_AGENT_HARNESS_RUN_ACTIVE'],
    ['too long', new HarnessConflictError('SESSION_HISTORY_TOO_LARGE', 'long'), 409, 'HTTP_AGENT_HARNESS_SESSION_HISTORY_TOO_LARGE'],
    ['copies unavailable', new HarnessUnavailableError('down'), 503, undefined],
    ['workspace too large', new WorkspaceError('WORKSPACE_TOO_LARGE', 'big'), 413, 'HTTP_AGENT_HARNESS_WORKSPACE_TOO_LARGE'],
    ['source being erased', new WorkspaceError('WORKSPACE_KEY_DESTROYED', 'gone'), 410, 'HTTP_AGENT_HARNESS_WORKSPACE_KEY_DESTROYED'],
  ] as const) {
    it(`${name}: ${String(status)}`, async () => {
      const { answer } = await call({ fork: () => Promise.reject(error) });
      const mapped = answer.error as { statusCode?: number; code?: string };
      expect(mapped.statusCode).to.equal(status);
      if (code !== undefined) expect(mapped.code).to.equal(code);
    });
  }

  it('the body is strict and the key well formed', () => {
    const params = { conversationId: CONVERSATION };
    expect(conversationForkSchema.safeParse({ params, body: { idempotencyKey: 'k-1' } }).success).to.equal(true);
    expect(conversationForkSchema.safeParse({ params, body: { idempotencyKey: 'k-1', actorId: 'alice' } }).success).to.equal(false);
    expect(conversationForkSchema.safeParse({ params, body: { idempotencyKey: '' } }).success).to.equal(false);
    expect(conversationForkSchema.safeParse({ params, body: { idempotencyKey: 'has space' } }).success).to.equal(false);
    expect(conversationForkSchema.safeParse({ params, body: {} }).success).to.equal(false);
    expect(conversationForkSchema.safeParse({ params: { conversationId: 'nope' }, body: { idempotencyKey: 'k' } }).success).to.equal(false);
    expect(agentConversationForkSchema.safeParse({ params: { ...params, agentKey: 'agent-1' }, body: { idempotencyKey: 'k' } }).success).to.equal(true);
  });
});
