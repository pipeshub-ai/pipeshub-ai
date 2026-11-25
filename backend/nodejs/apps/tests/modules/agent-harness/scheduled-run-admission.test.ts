import { expect } from 'chai';
import { admissionRefusalReason } from '../../../src/modules/agent-harness/application/scheduled-run-admission';
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
