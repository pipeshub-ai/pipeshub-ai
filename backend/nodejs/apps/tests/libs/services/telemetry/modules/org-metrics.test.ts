import { expect } from 'chai';
import {
  setOrgsTotal,
  setOrgAuthMethods,
} from '../../../../../src/libs/services/telemetry/modules/org-metrics';
import { metricsBackend } from '../../../../../src/libs/services/telemetry/metrics-backend';

describe('telemetry modules/org-metrics', () => {
  describe('setOrgsTotal', () => {
    it('should publish one series per account type, with no domain label', async () => {
      setOrgsTotal([
        { accountType: 'business', count: 3 },
        { accountType: 'individual', count: 1 },
      ]);

      const text = await metricsBackend.serialize();
      expect(text).to.include('pipeshub_orgs_total{account_type="business"} 3');
      expect(text).to.include('pipeshub_orgs_total{account_type="individual"} 1');
      expect(text).to.not.match(/pipeshub_orgs_total\{[^}]*domain=/);
    });

    it('should replace previous series on refresh (gauge reset semantics)', async () => {
      setOrgsTotal([{ accountType: 'stale', count: 9 }]);
      setOrgsTotal([{ accountType: 'business', count: 2 }]);

      const text = await metricsBackend.serialize();
      expect(text).to.not.include('account_type="stale"');
      expect(text).to.include(
        'pipeshub_orgs_total{account_type="business"} 2',
      );
    });

    it('should map an empty accountType to "unknown"', async () => {
      setOrgsTotal([{ accountType: '', count: 4 }]);

      const text = await metricsBackend.serialize();
      expect(text).to.include(
        'pipeshub_orgs_total{account_type="unknown"} 4',
      );
    });
  });

  describe('setOrgAuthMethods', () => {
    it('should publish value 1 per enabled org/method pair and reset stale pairs', async () => {
      setOrgAuthMethods([{ org: 'org-old', method: 'password' }]);
      setOrgAuthMethods([
        { org: 'org-1', method: 'saml' },
        { org: 'org-1', method: 'google' },
      ]);

      const text = await metricsBackend.serialize();
      expect(text).to.not.include('org="org-old"');
      expect(text).to.include(
        'pipeshub_org_auth_methods{org="org-1",method="saml"} 1',
      );
      expect(text).to.include(
        'pipeshub_org_auth_methods{org="org-1",method="google"} 1',
      );
    });
  });
});
