import { expect } from 'chai';
import { setOrgUsers } from '../../../../../src/libs/services/telemetry/modules/user-metrics';
import { metricsBackend } from '../../../../../src/libs/services/telemetry/metrics-backend';

describe('telemetry modules/user-metrics', () => {
  it('should publish user counts per org, with no domain label', async () => {
    setOrgUsers([
      { org: 'org-1', count: 12 },
      { org: 'org-2', count: 5 },
    ]);

    const text = await metricsBackend.serialize();
    expect(text).to.include('pipeshub_org_users{org="org-1"} 12');
    expect(text).to.include('pipeshub_org_users{org="org-2"} 5');
    expect(text).to.not.match(/pipeshub_org_users\{[^}]*domain=/);
  });

  it('should replace previous series on refresh', async () => {
    setOrgUsers([{ org: 'org-stale', count: 1 }]);
    setOrgUsers([{ org: 'org-3', count: 8 }]);

    const text = await metricsBackend.serialize();
    expect(text).to.not.include('org="org-stale"');
    expect(text).to.include('pipeshub_org_users{org="org-3"} 8');
  });
});
