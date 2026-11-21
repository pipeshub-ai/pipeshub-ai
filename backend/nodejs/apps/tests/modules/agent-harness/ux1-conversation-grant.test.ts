/**
 * UX1 CG-1, CG-2 (route and service): "Allow for this conversation" is an
 * approval with `scope: conversation`. The body refuses it on a denial; the
 * session service passes it on with its own command id, leaving a one-time
 * decision's id as it was. The store rule (offered by the worker, journaled
 * with the decision) is in the execution-store contract.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import {
  DecideInteraction,
  decisionCommandId,
  SessionInteractionService,
} from '../../../src/modules/agent-harness/application/session-interactions';
import { InMemoryApproverDirectory } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { resolutionJournalEvents } from '../../../src/modules/agent-harness/domain/interactions';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { decideSessionInteractionSchema } from '../../../src/modules/agent-harness/transport/schemas';

const params = { agentKey: 'a1', sessionId: 's1', runId: 'r1', interactionId: 'ix.approve.t1.0' };

describe('UX1: allow for this conversation', () => {
  it('CG-2: the body takes scope once or conversation, and conversation only with an approval', () => {
    const parse = (body: Record<string, unknown>) => decideSessionInteractionSchema.safeParse({ params, body }).success;
    expect(parse({ decision: 'approved', idempotencyKey: 'k1', scope: 'conversation' })).to.equal(true);
    expect(parse({ decision: 'approved', idempotencyKey: 'k1', scope: 'once' })).to.equal(true);
    expect(parse({ decision: 'denied', idempotencyKey: 'k1', scope: 'once' })).to.equal(true);
    expect(parse({ decision: 'denied', idempotencyKey: 'k1', scope: 'conversation' })).to.equal(false);
    expect(parse({ decision: 'approved', idempotencyKey: 'k1', scope: 'forever' })).to.equal(false);
  });

  it('CG-1: the session service passes the scope on, under its own command id', async () => {
    const run = { orgId: 'org-1', sessionId: 'ses-1', runId: 'run-1', actorId: 'u-actor', harnessMajor: 2, status: 'running' } as unknown as HarnessRun;
    const resolve = sinon.stub().resolves({ commandId: 'c', run, firstEventSeq: 1, lastEventSeq: 1 });
    const service = new SessionInteractionService({
      executions: { get: sinon.stub().resolves(run) } as never,
      interactions: { resolve } as never,
      sessions: { findStreamAccess: sinon.stub().resolves({ audience: 'owner' }) } as never,
      roles: { roleOf: sinon.stub().resolves('owner') },
      approvers: new InMemoryApproverDirectory(new Map(), new Map()),
    });
    const once: DecideInteraction = {
      orgId: 'org-1',
      actorId: 'u-actor',
      sessionId: 'ses-1',
      runId: 'run-1',
      interactionId: 'ix.approve.t1.0',
      decision: 'approved',
      idempotencyKey: 'k-1',
      channel: 'web',
    };
    const conversation: DecideInteraction = { ...once, scope: 'conversation' };
    await service.decide(conversation);
    expect(resolve.firstCall.args[0]).to.include({ scope: 'conversation', decision: 'approved' });
    expect(resolve.firstCall.args[0].commandId).to.equal(decisionCommandId(conversation));
    expect(decisionCommandId(conversation)).not.to.equal(decisionCommandId(once));
    await service.decide(once);
    expect(resolve.secondCall.args[0]).not.to.have.property('scope');
  });

  it('CG-1: the granted event follows the decision and precedes a resume; a denial never carries it', () => {
    const resolution = { interactionId: 'ix.approve.t1.0', decision: 'approved' as const, channel: 'web' as const, scope: 'conversation' as const };
    const suspended = resolutionJournalEvents({ status: 'waiting_approval' }, resolution, 'p_u');
    expect(suspended.map((e) => e.type)).to.deep.equal(['interaction.resolved', 'interaction.granted', 'run.resumed']);
    expect(suspended[1]?.critical).to.equal(false);
    const denied = resolutionJournalEvents({ status: 'running' }, { ...resolution, decision: 'denied' }, 'p_u');
    expect(denied.map((e) => e.type)).to.deep.equal(['interaction.resolved']);
  });

  it('CG-2: the refusal reaches the client as 409 with its code', () => {
    const error = transportError(new HarnessConflictError('CONVERSATION_GRANT_NOT_OFFERED', 'This call can only be allowed once'));
    expect(error).to.include({ statusCode: 409 });
    expect(String((error as { code?: unknown }).code)).to.contain('CONVERSATION_GRANT_NOT_OFFERED');
  });
});
