import { expect } from 'chai';
import {
  admissionRefusalReason,
  RunAdmissionScheduledRuns,
} from '../../../src/modules/agent-harness/application/scheduled-run-admission';
import { decideChatModeRollout } from '../../../src/modules/agent-harness/domain/rollout';
import {
  HarnessConflictError,
  HarnessForbiddenError,
  HarnessModelAccessRefusedError,
  HarnessNotFoundError,
  HarnessQuotaExceededError,
  HarnessSessionRoleRefusedError,
} from '../../../src/modules/agent-harness/domain/errors';

describe('agent-harness scheduled firings: an admission refusal as the firing\'s reason (P8a)', () => {
  const cases: Array<[string, unknown, string]> = [
    ['the agent refused to the actor', new HarnessForbiddenError(), 'agent_forbidden'],
    ['a full quota', new HarnessQuotaExceededError('org', 'active_runs', 30), 'quota'],
    ['a model access refusal keeps its own code', new HarnessModelAccessRefusedError('model_not_allowed' as never, 'no'), 'model_not_allowed'],
    ['a session role refusal, lower case', new HarnessSessionRoleRefusedError('NOT_A_PARTICIPANT' as never, 'no'), 'not_a_participant'],
    ['a conflict, lower case', new HarnessConflictError('SESSION_BUSY' as never, 'busy'), 'session_busy'],
    ['a missing run', new HarnessNotFoundError(), 'not_found'],
    ['anything else is an outage, retried', new Error('mongo down'), 'unavailable'],
    ['not even an error', 'boom', 'unavailable'],
  ];
  for (const [label, error, reason] of cases) {
    it(`${label}: ${reason}`, () => {
      expect(admissionRefusalReason(error)).to.equal(reason);
    });
  }
});

describe('agent-harness scheduled firings ask for the harness (the assistant in Agent mode)', () => {
  const firing = {
    orgId: 'org-1',
    actorId: 'user-1',
    idempotencyKey: 'schedule:sch_1:o:2026-11-27T17:05:04.000Z',
    deadlineMs: Date.now() + 60_000,
    input: { query: 'Tell me a joke', model: { chatMode: 'agent' } },
    authorization: 'Bearer t',
    trigger: { scheduleId: 'sch_1', firingId: 'f-1', slotKey: 'o:2026-11-27T17:05:04.000Z' },
  };

  it('a firing is admitted as a turn that asked for the harness', async () => {
    const seen: Array<Record<string, unknown>> = [];
    const admissions = {
      submit: async (request: Record<string, unknown>) => {
        seen.push(request);
        return { route: 'durable', admission: { run: { runId: 'run-1' }, sessionId: 'ses-1' } };
      },
    };
    const outcome = await new RunAdmissionScheduledRuns(admissions as never).admit(firing as never);
    expect(outcome).to.deep.equal({ kind: 'admitted', runId: 'run-1', sessionId: 'ses-1' });
    expect(seen[0]).to.include({ via: 'trigger', durableRequested: true });
  });

  it('with that ask the assistant\'s Agent mode stays durable; without it, it would go to today\'s loop', () => {
    const org = { useHarness: true, reason: 'enabled' } as never;
    const allowed = { assistantModes: ['agent'] };
    expect(decideChatModeRollout(org, 'agent', true, { ...allowed, durableRequested: true }).useHarness).to.equal(true);
    const unasked = decideChatModeRollout(org, 'agent', true, allowed);
    expect(unasked.useHarness).to.equal(false);
    expect(unasked.reason).to.equal('chat_mode_not_supported');
  });
});
