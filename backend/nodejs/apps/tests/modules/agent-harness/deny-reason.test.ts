/**
 * AP-05 (C3.72): a person who declines a held call may say why, and the run is
 * told. The reason rides `interaction.resolved` as an inline `answerRef`, so the
 * worker reads it without a content store; only a denial carries one.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import {
  DecideInteraction,
  SessionInteractionService,
} from '../../../src/modules/agent-harness/application/session-interactions';
import { InteractionService } from '../../../src/modules/agent-harness/application/interaction-service';
import { InMemoryApproverDirectory } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import {
  controlJournalEvents,
  HarnessRun,
  MAX_DENY_REASON_CHARS,
  resolvedData,
} from '../../../src/modules/agent-harness/domain/execution';
import { resolutionJournalEvents } from '../../../src/modules/agent-harness/domain/interactions';
import { interactionResolvedEventSchema } from '../../../src/modules/agent-harness/contracts/generated/core/events/interaction-resolved';
import {
  decideSessionInteractionSchema,
  decideSessionInteractionsSchema,
} from '../../../src/modules/agent-harness/transport/schemas';

const inlineText = (ref: unknown): string => {
  const value = (ref as { ref: string }).ref;
  expect(value.startsWith('inline:')).to.equal(true);
  return Buffer.from(value.slice('inline:'.length), 'base64url').toString('utf8');
};

describe('AP-05: a denial carries the person’s reason to the run (C3.72)', () => {
  const resolution = {
    interactionId: 'ix.approve.t1.0',
    decision: 'denied' as const,
    channel: 'web' as const,
    reason: '  Use the staging project, not prod  ',
  };

  it('AP-05: the resolution journals the trimmed reason inline, and the event stays a valid contract event', () => {
    const [resolved] = resolutionJournalEvents({ status: 'running' }, resolution, 'p_u');
    expect(resolved?.type).to.equal('interaction.resolved');
    expect(inlineText(resolved?.data['answerRef'])).to.equal('Use the staging project, not prod');
    const envelope = {
      orgId: 'org_1',
      sessionId: 'ses_1',
      streamId: 'ses_1',
      seq: 3,
      runId: 'run_1',
      ts: '2026-10-27T10:00:00.000Z',
      writer: 'node',
      ...resolved,
    };
    expect(interactionResolvedEventSchema.safeParse(envelope).success).to.equal(true);
  });

  it('AP-05: a resume of a suspended run carries it too', () => {
    const run = { harnessMajor: 2, actorId: 'u1' } as unknown as HarnessRun;
    const [resolved, resumed] = controlJournalEvents(run, { action: 'resume', resolution, by: 'p_u' });
    expect(inlineText(resolved?.data['answerRef'])).to.equal('Use the staging project, not prod');
    expect(resumed?.type).to.equal('run.resumed');
  });

  it('AP-05: an approval, a blank reason or no reason journals no answerRef', () => {
    for (const r of [
      { ...resolution, decision: 'approved' as const },
      { ...resolution, reason: '   ' },
      { interactionId: 'ix.approve.t1.0', decision: 'denied' as const, channel: 'web' as const },
    ]) {
      expect(resolvedData(r, 'p_u')).not.to.have.property('answerRef');
    }
  });

  it('AP-05: the routes take a bounded reason with a denial only', () => {
    const params = { agentKey: 'a1', sessionId: 's1', runId: 'r1', interactionId: 'ix.approve.t1.0' };
    const ok = (body: Record<string, unknown>) =>
      decideSessionInteractionSchema.safeParse({ params, body }).success;
    expect(ok({ decision: 'denied', idempotencyKey: 'k', reason: 'Wrong recipient' })).to.equal(true);
    expect(ok({ decision: 'approved', idempotencyKey: 'k', reason: 'fine' })).to.equal(false);
    expect(ok({ decision: 'answered', idempotencyKey: 'k', reason: 'it ran' })).to.equal(false);
    expect(ok({ decision: 'denied', idempotencyKey: 'k', reason: '' })).to.equal(false);
    expect(ok({ decision: 'denied', idempotencyKey: 'k', reason: 'x'.repeat(MAX_DENY_REASON_CHARS + 1) })).to.equal(
      false,
    );
    const batch = (body: Record<string, unknown>) =>
      decideSessionInteractionsSchema.safeParse({
        params: { agentKey: 'a1', sessionId: 's1', runId: 'r1' },
        body: { interactionIds: ['ix.approve.a', 'ix.approve.b'], idempotencyKey: 'k', ...body },
      }).success;
    expect(batch({ decision: 'denied', reason: 'Not these files' })).to.equal(true);
    expect(batch({ decision: 'approved', reason: 'ok' })).to.equal(false);
  });

  it('AP-05: the session service hands a denial’s reason to resolveActive, and drops it from anything else', async () => {
    const run = {
      orgId: 'org-1',
      sessionId: 'ses-1',
      runId: 'run-1',
      actorId: 'u-actor',
      harnessMajor: 2,
      status: 'running',
    } as unknown as HarnessRun;
    const control = sinon
      .stub()
      .callsFake(async () => ({ commandId: 'c', run, firstEventSeq: 1, lastEventSeq: 1 }));
    const executions = {
      get: sinon.stub().resolves(run),
      controlReceipt: sinon.stub().resolves(null),
      control,
    };
    const service = new SessionInteractionService({
      executions: executions as never,
      interactions: new InteractionService(executions as never),
      sessions: { findStreamAccess: sinon.stub().resolves({ audience: 'owner' }) } as never,
      roles: { roleOf: sinon.stub().resolves('owner') },
      approvers: new InMemoryApproverDirectory(new Map(), new Map()),
    });
    const command: DecideInteraction = {
      orgId: 'org-1',
      actorId: 'u-actor',
      sessionId: 'ses-1',
      runId: 'run-1',
      interactionId: 'ix.approve.t1.0',
      decision: 'denied',
      idempotencyKey: 'k-1',
      channel: 'web',
      reason: 'Send it to the team channel instead',
    };
    await service.decide(command);
    expect(control.firstCall.args[0].resolution).to.include({
      decision: 'denied',
      reason: 'Send it to the team channel instead',
    });
    await service.decide({ ...command, decision: 'approved', idempotencyKey: 'k-2' });
    expect(control.secondCall.args[0].resolution).not.to.have.property('reason');
  });
});
