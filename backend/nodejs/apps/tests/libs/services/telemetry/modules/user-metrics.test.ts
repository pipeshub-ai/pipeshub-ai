import { expect } from 'chai';
import { setOrgUsers } from '../../../../../src/libs/services/telemetry/modules/user-metrics';
import { metricsBackend } from '../../../../../src/libs/services/telemetry/metrics-backend';

describe('telemetry modules/user-metrics', () => {
  it('should publish user counts per org with domain', async () => {
    setOrgUsers([
      { org: 'org-1', domain: 'acme.io', count: 12 },
      { org: 'org-2', domain: 'beta.co', count: 5 },
    ]);

    const text = await metricsBackend.serialize();
    expect(text).to.include(
      'pipeshub_org_users{org="org-1",domain="acme.io"} 12',
    );
    expect(text).to.include('pipeshub_org_users{org="org-2",domain="beta.co"} 5');
  });

  it('should replace previous series on refresh', async () => {
    setOrgUsers([{ org: 'org-stale', domain: 'gone.io', count: 1 }]);
    setOrgUsers([{ org: 'org-3', domain: 'fresh.io', count: 8 }]);

    const text = await metricsBackend.serialize();
    expect(text).to.not.include('org="org-stale"');
    expect(text).to.include('pipeshub_org_users{org="org-3",domain="fresh.io"} 8');
  });

  it('should map empty domain to "unknown"', async () => {
    setOrgUsers([{ org: 'org-4', domain: '', count: 2 }]);

    const text = await metricsBackend.serialize();
    expect(text).to.include('pipeshub_org_users{org="org-4",domain="unknown"} 2');
  });
});
