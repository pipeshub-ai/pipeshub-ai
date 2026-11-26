/**
 * WP-39 follow-ups to round 9.
 *
 * WP39-ES-1..3 (SEC-CR-07 x AP-06): edited approval arguments in which the shared pasted-input rules find a secret
 * are refused with EDIT_HAS_SECRET: nothing is decided, nothing journaled, and the refusal names only the kinds,
 * never the value. The secret is never masked silently into what the call would send.
 * WP39-EX-5..7 (G-40): the policy webhook's ask is explained by its own rule (`external_policy`), which Node names
 * to workers (`external_policy_asks`); it never holds a conversation grant and never joins a batch, keyed off the
 * rule, whatever else the reason says.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import {
  DecideInteraction,
  SessionInteractionService,
} from '../../../src/modules/agent-harness/application/session-interactions';
import { InteractionService } from '../../../src/modules/agent-harness/application/interaction-service';
import { InMemoryApproverDirectory } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { batchExclusion } from '../../../src/modules/agent-harness/domain/approval-pattern';
import {
  EXTERNAL_POLICY_ASKS_FEATURE,
  NODE_CAPABILITIES,
} from '../../../src/modules/agent-harness/domain/node-capabilities';
import { pastedSecretKindsIn } from '../../../src/modules/agent-harness/domain/pasted-secrets';
import { ERROR_CODES } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/errors';
import { interactionExplainedEventSchema } from '../../../src/modules/agent-harness/contracts/generated/core/events/interaction-explained';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { VECTOR_EXAMPLES } from './support/canary-scan';

const SECRET = VECTOR_EXAMPLES[0]!.secret;

function world() {
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
  const logged: unknown[] = [];
  const logger = {
    info: (...args: unknown[]) => logged.push(args),
    warn: (...args: unknown[]) => logged.push(args),
    error: (...args: unknown[]) => logged.push(args),
    debug: (...args: unknown[]) => logged.push(args),
  };
  const service = new SessionInteractionService({
    executions: executions as never,
    interactions: new InteractionService(executions as never),
    sessions: { findStreamAccess: sinon.stub().resolves({ audience: 'owner' }) } as never,
    roles: { roleOf: sinon.stub().resolves('owner') },
    approvers: new InMemoryApproverDirectory(new Map(), new Map()),
    logger: logger as never,
  });
  const command = (edits: Record<string, unknown>): DecideInteraction => ({
    orgId: 'org-1',
    actorId: 'u-actor',
    sessionId: 'ses-1',
    runId: 'run-1',
    interactionId: 'ix.approve.t1.0',
    decision: 'approved',
    idempotencyKey: 'k-1',
    channel: 'web',
    edits,
  });
  return { service, control, command, logged };
}

async function refusal(promise: Promise<unknown>): Promise<unknown> {
  try {
    await promise;
  } catch (error) {
    return error;
  }
  return undefined;
}

describe('WP-39: an edit with a pasted secret is refused (SEC-CR-07 x AP-06)', () => {
  it('WP39-ES-1: the rules find a secret anywhere in the edit: a value, a nested value, a list, a key', () => {
    expect(pastedSecretKindsIn({ body: `use ${SECRET} please` })).to.have.length(1);
    expect(pastedSecretKindsIn({ a: { b: [1, `x ${SECRET}`] } })).to.have.length(1);
    expect(pastedSecretKindsIn({ [SECRET]: 'v' })).to.have.length(1);
    expect(pastedSecretKindsIn({ to: 'team@acme.example', body: 'Summary only', n: 3, ok: true, none: null })).to.deep.equal([]);
  });

  it('WP39-ES-2: nothing is decided or journaled, and the log names the kinds, never the value', async () => {
    const { service, control, command, logged } = world();
    const error = await refusal(service.decide(command({ to: 'team@acme.example', body: `token ${SECRET}` })));
    expect(error).to.be.instanceOf(HarnessConflictError);
    expect((error as HarnessConflictError).code).to.equal('EDIT_HAS_SECRET');
    expect((error as Error).message).not.to.contain(SECRET);
    expect(control.called).to.equal(false);
    expect(JSON.stringify(logged)).to.contain('EDIT_HAS_SECRET').and.not.contain(SECRET);
    // The same person's clean edit goes through.
    await service.decide(command({ to: 'team@acme.example', body: 'Summary only' }));
    expect(control.calledOnce).to.equal(true);
  });

  it('WP39-ES-3: the refusal reaches the client as 422 EDIT_HAS_SECRET, telling them to save a credential', () => {
    const http = transportError(new HarnessConflictError('EDIT_HAS_SECRET', 'x'));
    expect(http).to.include({ statusCode: 422 });
    expect(String((http as { code?: unknown }).code)).to.contain('EDIT_HAS_SECRET');
    expect(ERROR_CODES.EDIT_HAS_SECRET.nextAction.text).to.contain('credential');
  });
});

describe('WP-39: the policy webhook asks under its own rule (G-40)', () => {
  const reason = {
    interactionId: 'ix.approve.t1.0',
    rule: 'external_policy',
    layer: 'org',
    toolId: 'slack/post_message',
    conversationGrant: false,
  };

  it('WP39-EX-5: Node names external_policy_asks to workers, and the event contract takes the rule', () => {
    expect(EXTERNAL_POLICY_ASKS_FEATURE).to.equal('external_policy_asks');
    expect(NODE_CAPABILITIES.features).to.include(EXTERNAL_POLICY_ASKS_FEATURE);
    const parsed = interactionExplainedEventSchema.shape.data.safeParse(reason);
    expect(parsed.success).to.equal(true);
  });

  it('WP39-EX-6: a webhook ask never joins a batch, even with a public context', () => {
    expect(
      batchExclusion({ toolName: 'post_message', args: '{}', reason: { rule: 'external_policy', sensitivity: 'public' } }),
    ).to.equal('external_policy');
    expect(batchExclusion({ toolName: 'post_message', args: '{}', reason: { rule: 'writes', sensitivity: 'public' } })).to.equal(
      null,
    );
  });
});
