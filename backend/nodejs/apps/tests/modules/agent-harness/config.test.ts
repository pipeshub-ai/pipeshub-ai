import { expect } from 'chai';
import {
  describeAgentHarnessReadiness,
  isAgentHarnessInfrastructureReady,
} from '../../../src/modules/agent-harness/config';

const ready = {
  REPLICA_SET_AVAILABLE: 'true',
  AGENT_HARNESS_RELAY_ENABLED: 'true',
  AGENT_HARNESS_WORKER_ENABLED: 'true',
};

describe('agent harness operational configuration', () => {
  it('is ready only with transactions, relay and worker', () => {
    expect(isAgentHarnessInfrastructureReady(ready)).to.equal(true);
    for (const name of Object.keys(ready)) {
      expect(
        isAgentHarnessInfrastructureReady({ ...ready, [name]: 'false' }),
        name,
      ).to.equal(false);
    }
  });

  it('treats absent readiness as unavailable', () => {
    expect(isAgentHarnessInfrastructureReady({})).to.equal(false);
  });

  it('stays off when the probed journal is not durable (standalone, PSA)', () => {
    for (const reason of ['standalone', 'arbiter', 'probe_failed'] as const) {
      expect(
        isAgentHarnessInfrastructureReady(ready, { durable: false, reason }),
        reason,
      ).to.equal(false);
    }
    expect(
      isAgentHarnessInfrastructureReady(ready, {
        durable: true,
        reason: 'majority',
      }),
    ).to.equal(true);
  });

  it('reports journal.durable in the readiness description', () => {
    expect(
      describeAgentHarnessReadiness(ready, {
        durable: false,
        reason: 'arbiter',
      }),
    ).to.deep.equal({
      ready: false,
      transactions: true,
      relay: true,
      worker: true,
      journal: { durable: false, reason: 'arbiter' },
    });
    expect(describeAgentHarnessReadiness(ready, null).journal).to.deep.equal({
      durable: null,
      reason: null,
    });
  });
});
