/**
 * QA-AUTO F1: a quota refusal names which quota refused it (`reasonCode`), so the chat words the active-run cap
 * ("one is answering already") apart from the per-minute rate ("you sent a lot"); the code and status stay.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { HarnessQuotaExceededError } from '../../../src/modules/agent-harness/domain/errors';
import { transportError } from '../../../src/modules/agent-harness/transport/errors';
import { HttpError } from '../../../src/libs/errors/http.errors';
import { REASON_CODE_PATTERN } from '../../../src/libs/errors/backend-error';

describe('QA-AUTO F1: the quota refusal names its quota', () => {
  for (const [quota, scope] of [['active_runs', 'user'], ['active_runs', 'org'], ['admission_rate', 'user'], ['admission_rate', 'org']] as const) {
    it(`${quota} for the ${scope}: reasonCode ${quota}_${scope}, Retry-After kept`, () => {
      const error = transportError(new HarnessQuotaExceededError(scope, quota, 7)) as HttpError;
      expect(error).to.be.instanceOf(HttpError);
      expect(error.statusCode).to.equal(429);
      expect(error.code).to.equal('HTTP_AGENT_HARNESS_QUOTA_EXCEEDED');
      expect(error.metadata).to.deep.equal({ retryAfter: '7', reasonCode: `${quota}_${scope}` });
      // The error middleware relays only a reason code of this shape.
      expect(REASON_CODE_PATTERN.test(`${quota}_${scope}`)).to.equal(true);
    });
  }
});
