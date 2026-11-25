/**
 * AP-06 (C3.75): edit-then-approve. The person the run acts for approves a held call with changed arguments;
 * the decision journals them as an inline `answerRef` (canonical JSON, with its hash), the audit chain records
 * that hash beside the call's own `argsDigest`, and only the actor may edit. The store rule (offered by the
 * worker, and the run then needing `approval_edits`) is in the execution-store contract.
 */
import { expect } from 'chai';
import { createHash } from 'node:crypto';
import sinon from 'sinon';
import {
  DecideInteraction,
  decisionCommandId,
  SessionInteractionService,
} from '../../../src/modules/agent-harness/application/session-interactions';
import { InteractionService } from '../../../src/modules/agent-harness/application/interaction-service';
import { InMemoryApproverDirectory } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import { auditProjection } from '../../../src/modules/agent-harness/domain/audit';
import { HarnessConflictError, HarnessDecisionRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import {
  HarnessRun,
  MAX_EDITED_ARGS_BYTES,
  resolvedData,
} from '../../../src/modules/agent-harness/domain/execution';
import {
  resolutionFeatures,
  resolutionJournalEvents,
} from '../../../src/modules/agent-harness/domain/interactions';
import { NODE_CAPABILITIES } from '../../../src/modules/agent-harness/domain/node-capabilities';
import { interactionResolvedEventSchema } from '../../../src/modules/agent-harness/contracts/generated/core/events/interaction-resolved';
import { canonicalJsonBytes } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import {
  decideSessionInteractionSchema,
  decideSessionInteractionsSchema,
} from '../../../src/modules/agent-harness/transport/schemas';

const EDITS = { to: 'team@acme.example', body: 'Summary only' };
const params = { agentKey: 'a1', sessionId: 's1', runId: 'r1', interactionId: 'ix.approve.t1.0' };
const resolution = {
  interactionId: 'ix.approve.t1.0',
  decision: 'approved' as const,
  channel: 'web' as const,
  edits: EDITS,
};

const sha256 = (bytes: Buffer): string => `sha256:${createHash('sha256').update(bytes).digest('hex')}`;

function world(actorId = 'u-actor', isApprover = false) {
  const run = {
    orgId: 'org-1',
    sessionId: 'ses-1',
    runId: 'run-1',
    actorId: 'u-actor',
    harnessMajor: 2,
    status: 'running',
  } as unknown as HarnessRun;
  const control = sinon.stub().callsFake(async () => ({ commandId: 'c', run, firstEventSeq: 1, lastEventSeq: 1 }));
  const executions = {
    get: sinon.stub().resolves(run),
    controlReceipt: sinon.stub().resolves(null),
    control,
  };
  const service = new SessionInteractionService({
    executions: executions as never,
    interactions: new InteractionService(executions as never),
    sessions: { findStreamAccess: sinon.stub().resolves({ audience: 'owner' }) } as never,
    roles: { roleOf: sinon.stub().resolves(actorId === 'u-actor' ? 'owner' : 'viewer') },
    approvers: new InMemoryApproverDirectory(
      new Map(isApprover ? [['org-1', new Set([actorId])]] : []),
      new Map(),
    ),
  });
  const command: DecideInteraction = {
    orgId: 'org-1',
    actorId,
    sessionId: 'ses-1',
    runId: 'run-1',
    interactionId: 'ix.approve.t1.0',
    decision: 'approved',
    idempotencyKey: 'k-1',
    channel: 'web',
    edits: EDITS,
  };
  return { service, control, command };
}

describe('AP-06: edit-then-approve (C3.75)', () => {
  it('AP-06: the approval journals the edit as canonical JSON inline, its hash the bytes’ own', () => {
    const data = resolvedData(resolution, 'p_u');
    const ref = data['answerRef'] as { ref: string; hash: string; size: number };
    const bytes = Buffer.from(ref.ref.slice('inline:'.length), 'base64url');
    expect(bytes.equals(canonicalJsonBytes(EDITS))).to.equal(true);
    expect(ref).to.include({ hash: sha256(bytes), size: bytes.length });
    expect(resolvedData({ ...resolution, decision: 'denied' }, 'p_u')).not.to.have.property('answerRef');
  });

  it('AP-06: the audit chain records the edit’s hash, never the edit', () => {
    const [resolved] = resolutionJournalEvents({ status: 'running' }, resolution, 'p_u');
    const envelope = {
      orgId: 'org_1',
      sessionId: 'ses_1',
      streamId: 'ses_1',
      seq: 3,
      runId: 'run_1',
      ts: '2026-10-30T10:00:00.000Z',
      writer: 'node',
      ...resolved,
    };
    delete (envelope as Record<string, unknown>)['auditFields'];
    expect(interactionResolvedEventSchema.safeParse(envelope).success).to.equal(true);
    const projected = auditProjection('interaction.resolved', resolved!.data, resolved!.auditFields);
    expect(projected?.fields).to.deep.include({
      decision: 'approved',
      editedArgsDigest: sha256(canonicalJsonBytes(EDITS)),
    });
    expect(JSON.stringify(projected?.fields)).not.to.contain('Summary only');
    const plain = resolutionJournalEvents({ status: 'running' }, { ...resolution, edits: undefined }, 'p_u');
    expect(plain[0]?.auditFields).to.equal(undefined);
  });

  it('AP-06: an approved edit makes the run need approval_edits; nothing else does', () => {
    expect(resolutionFeatures([resolution])).to.deep.equal(['approval_edits']);
    expect(resolutionFeatures([{ ...resolution, edits: undefined }])).to.deep.equal([]);
    expect(resolutionFeatures([{ ...resolution, decision: 'denied' }])).to.deep.equal([]);
    expect(NODE_CAPABILITIES.features).to.include('approval_edits');
  });

  it('AP-06: the route takes a bounded JSON object with an approval of one call only', () => {
    const ok = (body: Record<string, unknown>) => decideSessionInteractionSchema.safeParse({ params, body }).success;
    expect(ok({ decision: 'approved', idempotencyKey: 'k', edits: EDITS })).to.equal(true);
    expect(ok({ decision: 'denied', idempotencyKey: 'k', edits: EDITS })).to.equal(false);
    expect(ok({ decision: 'approved', idempotencyKey: 'k', edits: EDITS, scope: 'conversation' })).to.equal(false);
    expect(ok({ decision: 'approved', idempotencyKey: 'k', edits: EDITS, remember: true })).to.equal(false);
    expect(ok({ decision: 'approved', idempotencyKey: 'k', edits: ['a'] })).to.equal(false);
    expect(ok({ decision: 'approved', idempotencyKey: 'k', edits: { body: 'x'.repeat(MAX_EDITED_ARGS_BYTES) } })).to.equal(
      false,
    );
    const batch = decideSessionInteractionsSchema.safeParse({
      params: { agentKey: 'a1', sessionId: 's1', runId: 'r1' },
      body: { interactionIds: ['ix.a', 'ix.b'], decision: 'approved', idempotencyKey: 'k', edits: EDITS },
    });
    expect(batch.success).to.equal(false);
  });

  it('AP-06: the actor’s edit reaches resolveActive under its own command id', async () => {
    const { service, control, command } = world();
    await service.decide(command);
    expect(control.firstCall.args[0].resolution).to.deep.include({ decision: 'approved', edits: EDITS });
    const asked = { ...command, edits: undefined };
    expect(decisionCommandId(command)).not.to.equal(decisionCommandId(asked));
    expect(decisionCommandId({ ...command, edits: { ...EDITS, body: 'other' } })).not.to.equal(
      decisionCommandId(command),
    );
  });

  it('AP-06: an approver who is not the actor may approve as asked, never edit', async () => {
    const { service, control, command } = world('u-approver', true);
    let refused: unknown;
    try {
      await service.decide(command);
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessDecisionRefusedError);
    expect((refused as HarnessDecisionRefusedError).code).to.equal('EDIT_NOT_ALLOWED');
    expect(control.called).to.equal(false);
    await service.decide({ ...command, edits: undefined });
    expect(control.firstCall.args[0].resolution).not.to.have.property('edits');
  });

  it('AP-06: a batch never carries an edit', async () => {
    const { service, command, control } = world();
    const { interactionId: _id, ...rest } = command;
    _id;
    let refused: unknown;
    try {
      await service.decideAll({ ...rest, interactionIds: ['ix.a', 'ix.b'] });
    } catch (error) {
      refused = error;
    }
    expect((refused as HarnessConflictError).code).to.equal('EDIT_NOT_OFFERED');
    expect(control.called).to.equal(false);
  });

  it('AP-06: the refusals reach the client with their codes', () => {
    const offered = transportError(new HarnessConflictError('EDIT_NOT_OFFERED', 'This call can only be approved as it is'));
    expect(offered).to.include({ statusCode: 409 });
    expect(String((offered as { code?: unknown }).code)).to.contain('EDIT_NOT_OFFERED');
    const actor = transportError(new HarnessDecisionRefusedError('EDIT_NOT_ALLOWED', 'x'));
    expect(actor).to.include({ statusCode: 403 });
  });
});
