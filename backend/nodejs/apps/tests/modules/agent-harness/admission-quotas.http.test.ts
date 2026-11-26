/** P-5 over HTTP: a full quota is 429 with Retry-After, and nothing is written. */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { ADMISSION_QUOTAS } from '../../../src/modules/agent-harness/domain/admission-quotas';
import { fixedRollout } from './support/rollout';
import { grantingAccess } from './support/agent-definition';
import {
  admissionBody,
  AdmissionHttp,
  startAdmissionHttp,
} from './support/admission-http';

describe('P-5: admission quotas over HTTP', () => {
  let http: AdmissionHttp;
  let store: InMemoryExecutionStore;

  beforeEach(async () => {
    store = new InMemoryExecutionStore(undefined, undefined, {
      ...ADMISSION_QUOTAS,
      activeRunsPerUser: 1,
      activeRetryAfterS: 20,
    });
    http = await startAdmissionHttp(
      new RunAdmissionService({
        store,
        rollout: fixedRollout(),
        agentAccess: grantingAccess(),
      }),
      { user: { orgId: 'org-1', userId: 'user-1' } },
    );
  });

  afterEach(async () => {
    await http.close();
  });

  it('answers 429 with Retry-After and a stable code, and admits nothing', async () => {
    const first = await http.post(
      '/agents/agent-1/runs',
      'user',
      admissionBody(),
    );
    expect(first.status).to.equal(202);

    const refused = await http.post(
      '/agents/agent-1/runs',
      'user',
      admissionBody(),
    );
    expect(refused.status).to.equal(429);
    expect(refused.headers.get('retry-after')).to.equal('20');
    expect(refused.body.error.code).to.equal(
      'HTTP_AGENT_HARNESS_QUOTA_EXCEEDED',
    );
    expect(store.dispatches).to.have.length(1);
  });
});
