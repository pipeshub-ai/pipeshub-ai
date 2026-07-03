import { expect } from 'chai';
import {
  setOrgsTotal,
  setOrgAuthMethods,
} from '../../../../../src/libs/services/telemetry/modules/org-metrics';
import { metricsBackend } from '../../../../../src/libs/services/telemetry/metrics-backend';

describe('telemetry modules/org-metrics', () => {
  describe('setOrgsTotal', () => {
    it('should publish one series per account-type/domain pair', async () => {
      setOrgsTotal([
        { accountType: 'business', domain: 'acme.io', count: 3 },
        { accountType: 'individual', domain: 'solo.dev', count: 1 },
      ]);

      const text = await metricsBackend.serialize();
      expect(text).to.include(
        'pipeshub_orgs_total{account_type="business",domain="acme.io"} 3',
      );
      expect(text).to.include(
        'pipeshub_orgs_total{account_type="individual",domain="solo.dev"} 1',
      );
    });

    it('should replace previous series on refresh (gauge reset semantics)', async () => {
      setOrgsTotal([{ accountType: 'stale', domain: 'old.io', count: 9 }]);
      setOrgsTotal([{ accountType: 'business', domain: 'new.io', count: 2 }]);

      const text = await metricsBackend.serialize();
      expect(text).to.not.include('account_type="stale"');
      expect(text).to.include(
        'pipeshub_orgs_total{account_type="business",domain="new.io"} 2',
      );
    });

    it('should map empty accountType/domain to "unknown"', async () => {
      setOrgsTotal([{ accountType: '', domain: '', count: 4 }]);

      const text = await metricsBackend.serialize();
      expect(text).to.include(
        'pipeshub_orgs_total{account_type="unknown",domain="unknown"} 4',
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
