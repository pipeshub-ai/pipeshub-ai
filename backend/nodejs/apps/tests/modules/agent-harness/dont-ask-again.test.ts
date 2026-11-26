/**
 * BUGS-B (SYNC4 §8/§10): main's "Don't ask again" on a durable approval card.
 * The worker offers it (`interaction.explained` `ruleGrant`) only where main's
 * default rule for the person's own MCP tool asked (PW-094: never an explicit
 * rule, never a toolset's, which are the admin's). The decision then saves the
 * person's rule through main's own route, as the person, after the decision
 * commits: Allow saves allow, Deny saves block, and a Block saved since is
 * never turned into an Allow.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { AddressInfo } from 'node:net';
import { createServer, Server } from 'node:http';
import {
  DecideInteraction,
  decisionCommandId,
  SessionInteractionService,
} from '../../../src/modules/agent-harness/application/session-interactions';
import { InMemoryApproverDirectory } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import { ConnectorToolRuleWriter } from '../../../src/modules/agent-harness/adapters/connector-tool-rule-writer';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { NODE_CAPABILITIES } from '../../../src/modules/agent-harness/domain/node-capabilities';
import { approvalReason } from '../../../src/modules/agent-harness/projection/timeline';
import {
  decideSessionInteractionSchema,
  decideSessionInteractionsSchema,
} from '../../../src/modules/agent-harness/transport/schemas';
import { registerUpstreams } from '../../../src/libs/http/upstream-call';

const params = { agentKey: 'a1', sessionId: 's1', runId: 'r1', interactionId: 'ix.approve.t1.0' };
const GRANT = { instanceId: 'mcp-9', toolName: 'list_prs' };
const AUTH = 'Bearer person-token';

const run = {
  orgId: 'org-1',
  sessionId: 'ses-1',
  runId: 'run-1',
  actorId: 'u-actor',
  harnessMajor: 2,
  status: 'waiting_approval',
} as unknown as HarnessRun;

const decision = (extra: Partial<DecideInteraction> = {}): DecideInteraction => ({
  orgId: 'org-1',
  actorId: 'u-actor',
  sessionId: 'ses-1',
  runId: 'run-1',
  interactionId: 'ix.approve.t1.0',
  decision: 'approved',
  idempotencyKey: 'k-1',
  channel: 'web',
  remember: true,
  authorization: AUTH,
  ...extra,
});

function service(offered: typeof GRANT | null, write = sinon.stub().resolves(true)) {
  const resolve = sinon.stub().resolves({ commandId: 'c', run, firstEventSeq: 1, lastEventSeq: 1 });
  const svc = new SessionInteractionService({
    executions: {
      get: sinon.stub().resolves(run),
      ruleGrantOffered: sinon.stub().resolves(offered),
    } as never,
    interactions: { resolve } as never,
    sessions: { findStreamAccess: sinon.stub().resolves({ audience: 'owner' }) } as never,
    roles: { roleOf: sinon.stub().resolves('owner') },
    approvers: new InMemoryApproverDirectory(new Map([['org-1', new Set(['u-approver'])]]), new Map()),
    toolRules: { remember: write },
  });
  return { svc, resolve, write };
}

describe('BUGS-B: "Don\'t ask again" on a durable approval card', () => {
  it('DA-1: the body takes remember with an approval or a denial, never with an answer or a conversation grant', () => {
    const parse = (body: Record<string, unknown>) => decideSessionInteractionSchema.safeParse({ params, body }).success;
    expect(parse({ decision: 'approved', idempotencyKey: 'k1', remember: true })).to.equal(true);
    expect(parse({ decision: 'denied', idempotencyKey: 'k1', remember: true })).to.equal(true);
    expect(parse({ decision: 'answered', idempotencyKey: 'k1', remember: true })).to.equal(false);
    expect(parse({ decision: 'approved', idempotencyKey: 'k1', remember: true, scope: 'conversation' })).to.equal(false);
  });

  it('DA-2: an approval the worker offered it for saves allow as the person, after the decision', async () => {
    const { svc, resolve, write } = service(GRANT);
    await svc.decide(decision());
    expect(write.calledOnceWithExactly(AUTH, GRANT, 'allow')).to.equal(true);
    expect(write.calledAfter(resolve)).to.equal(true);
    // The decision itself is the one-time decision it always was: a retry without remember replays it.
    expect(resolve.firstCall.args[0].commandId).to.equal(decisionCommandId(decision({ remember: undefined })));
  });

  it('DA-3: a denial saves block', async () => {
    const { svc, write } = service(GRANT);
    await svc.decide(decision({ decision: 'denied' }));
    expect(write.calledOnceWithExactly(AUTH, GRANT, 'block')).to.equal(true);
  });

  it('DA-4: not offered (an explicit or an admin rule, a toolset, an older worker): refused, nothing decided or saved', async () => {
    const { svc, resolve, write } = service(null);
    const refused = await svc.decide(decision()).catch((error: unknown) => error);
    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal('CONVERSATION_GRANT_NOT_OFFERED');
    expect(resolve.called).to.equal(false);
    expect(write.called).to.equal(false);
  });

  it('DA-5: only the person the run acts for saves their rule; an approver deciding for them cannot', async () => {
    const { svc, resolve, write } = service(GRANT);
    const refused = await svc
      .decide(decision({ actorId: 'u-approver' }))
      .catch((error: unknown) => error);
    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect(resolve.called).to.equal(false);
    expect(write.called).to.equal(false);
  });

  it('DA-6: a rule that cannot be saved never undoes the decision', async () => {
    const { svc } = service(GRANT, sinon.stub().rejects(new Error('down')));
    const decided = await svc.decide(decision());
    expect(decided.interaction.decision).to.equal('approved');
  });

  it('DA-11: a batch ("Allow all N", DECIDE409) never saves a rule: refused with its 409, nothing decided', async () => {
    const { svc, resolve, write } = service(GRANT);
    const { interactionId, ...rest } = decision();
    const refused = await svc
      .decideAll({ ...rest, interactionIds: [interactionId, 'ix.approve.t1.1'] })
      .catch((error: unknown) => error);
    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal('CONVERSATION_GRANT_NOT_OFFERED');
    expect(resolve.called).to.equal(false);
    expect(write.called).to.equal(false);
    const parse = (body: Record<string, unknown>) =>
      decideSessionInteractionsSchema.safeParse({ params, body }).success;
    // Named in the body so the refusal is that 409, not a bare validation error.
    expect(parse({ interactionIds: ['a', 'b'], decision: 'approved', idempotencyKey: 'k', remember: true })).to.equal(true);
  });

  it('DA-12: a retried "Don\'t ask again" whose decision stands saves the same rule again; one decided elsewhere saves none', async () => {
    const { svc, write } = service(GRANT);
    await svc.decide(decision());
    await svc.decide(decision());
    expect(write.callCount).to.equal(2);
    expect(write.alwaysCalledWithExactly(AUTH, GRANT, 'allow')).to.equal(true);

    const elsewhere = sinon.stub().resolves(true);
    const decidedElsewhere = new SessionInteractionService({
      executions: {
        get: sinon.stub().resolves(run),
        ruleGrantOffered: sinon.stub().resolves(GRANT),
        interactionDecisions: sinon.stub().resolves(new Map([['ix.approve.t1.0', 'denied']])),
      } as never,
      interactions: {
        resolve: sinon.stub().rejects(new HarnessConflictError('INTERACTION_NOT_OPEN', 'decided')),
      } as never,
      sessions: { findStreamAccess: sinon.stub().resolves({ audience: 'owner' }) } as never,
      roles: { roleOf: sinon.stub().resolves('owner') },
      approvers: new InMemoryApproverDirectory(new Map(), new Map()),
      toolRules: { remember: elsewhere },
    });
    const outcome = await decidedElsewhere.decide(decision());
    expect(outcome.interaction).to.include({ decision: 'denied', alreadyDecided: true });
    expect(elsewhere.called).to.equal(false);
  });

  it('DA-7: the card is told what the worker offered; a Node advertises it to workers', () => {
    expect(approvalReason({ rule: 'writes', toolId: 'mcp@mcp-9/list_prs', conversationGrant: false, ruleGrant: GRANT }))
      .to.include({ ruleGrant: true });
    expect(approvalReason({ rule: 'writes', toolId: 'jira@i/create', conversationGrant: false }))
      .not.to.have.property('ruleGrant');
    expect(NODE_CAPABILITIES.features).to.include('approval_rule_grants');
  });
});

describe('BUGS-B: ConnectorToolRuleWriter', () => {
  const PATH = '/api/v1/mcp-servers/instances/mcp-9/my-tool-rules';
  let server: Server;
  let base: string;
  let stored: { status: number; body: unknown } = { status: 200, body: { tools: {} } };
  const seen: Array<{ method: string; url: string; authorization?: string; body: string }> = [];

  before(async () => {
    server = createServer((req, res) => {
      let body = '';
      req.on('data', (chunk: Buffer) => (body += chunk.toString()));
      req.on('end', () => {
        seen.push({ method: req.method ?? '', url: req.url ?? '', authorization: req.headers.authorization, body });
        res.writeHead(req.method === 'PUT' ? 200 : stored.status, { 'content-type': 'application/json' });
        res.end(req.method === 'PUT' ? body : JSON.stringify(stored.body));
      });
    });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    registerUpstreams({ query: [base] });
  });

  after(() => {
    registerUpstreams({});
    server.close();
  });

  beforeEach(() => {
    seen.length = 0;
  });

  it("DA-8: merges the tool's rule into the person's rules, as the person", async () => {
    stored = { status: 200, body: { tools: { other: 'ask' } } };
    expect(await new ConnectorToolRuleWriter(() => base).remember(AUTH, GRANT, 'allow')).to.equal(true);
    expect(seen.map((r) => [r.method, r.url, r.authorization])).to.deep.equal([
      ['GET', PATH, AUTH],
      ['PUT', PATH, AUTH],
    ]);
    expect(JSON.parse(seen[1]!.body)).to.deep.equal({ tools: { other: 'ask', list_prs: 'allow' } });
  });

  it('DA-9: an Allow never replaces a Block saved since the card was shown', async () => {
    stored = { status: 200, body: { tools: { list_prs: 'block' } } };
    expect(await new ConnectorToolRuleWriter(() => base).remember(AUTH, GRANT, 'allow')).to.equal(false);
    expect(seen.map((r) => r.method)).to.deep.equal(['GET']);
  });

  it('DA-10: a refused read saves nothing', async () => {
    stored = { status: 403, body: { detail: 'no' } };
    expect(await new ConnectorToolRuleWriter(() => base).remember(AUTH, GRANT, 'block')).to.equal(false);
    expect(seen.map((r) => r.method)).to.deep.equal(['GET']);
  });
});
