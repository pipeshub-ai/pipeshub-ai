import { expect } from 'chai';
import {
  ADMISSION_QUOTAS,
  advanceWindow,
  assertAdmissionQuotas,
} from '../../../src/modules/agent-harness/domain/admission-quotas';
import { HarnessQuotaExceededError } from '../../../src/modules/agent-harness/domain/errors';

const refusal = (work: () => void): HarnessQuotaExceededError => {
  try {
    work();
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessQuotaExceededError);
    return error as HarnessQuotaExceededError;
  }
  return expect.fail('expected a quota refusal');
};

describe('admission quota rules', () => {
  it('opens a window on first use and a new one once it lapsed', () => {
    expect(advanceWindow(null, 5, 60)).to.deep.equal({
      windowStart: 5,
      count: 1,
    });
    expect(advanceWindow({ windowStart: 5, count: 3 }, 64, 60)).to.deep.equal({
      windowStart: 5,
      count: 4,
    });
    expect(advanceWindow({ windowStart: 5, count: 3 }, 65, 60)).to.deep.equal({
      windowStart: 65,
      count: 1,
    });
  });

  const within = {
    orgWindow: { windowStart: 0, count: 1 },
    userWindow: { windowStart: 0, count: 1 },
    orgActive: 0,
    userActive: 0,
  };

  it('admits up to each limit, inclusive for the rate and exclusive for active runs', () => {
    expect(() =>
      assertAdmissionQuotas(
        {
          orgWindow: {
            windowStart: 0,
            count: ADMISSION_QUOTAS.admissionsPerOrg,
          },
          userWindow: {
            windowStart: 0,
            count: ADMISSION_QUOTAS.admissionsPerUser,
          },
          orgActive: ADMISSION_QUOTAS.activeRunsPerOrg - 1,
          userActive: ADMISSION_QUOTAS.activeRunsPerUser - 1,
        },
        0,
        ADMISSION_QUOTAS,
      ),
    ).not.to.throw();
  });

  it('names the quota that refused and when to retry', () => {
    const active = refusal(() =>
      assertAdmissionQuotas(
        { ...within, userActive: ADMISSION_QUOTAS.activeRunsPerUser },
        0,
        ADMISSION_QUOTAS,
      ),
    );
    expect([active.scope, active.quota, active.retryAfterS]).to.deep.equal([
      'user',
      'active_runs',
      ADMISSION_QUOTAS.activeRetryAfterS,
    ]);
    const rate = refusal(() =>
      assertAdmissionQuotas(
        {
          ...within,
          orgWindow: {
            windowStart: 1_000,
            count: ADMISSION_QUOTAS.admissionsPerOrg + 1,
          },
        },
        59_500,
        ADMISSION_QUOTAS,
      ),
    );
    expect([rate.scope, rate.quota, rate.retryAfterS]).to.deep.equal([
      'org',
      'admission_rate',
      2,
    ]);
  });
});
