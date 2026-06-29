import { metricsBackend } from '../metrics-backend';

const orgsTotal = metricsBackend.createGauge({
  name: 'pipeshub_orgs_total',
  help: 'Current number of (non-deleted) organizations',
  labelNames: ['account_type', 'domain'],
});

const orgAuthMethods = metricsBackend.createGauge({
  name: 'pipeshub_org_auth_methods',
  help: 'Enabled authentication methods per organization (1 = enabled)',
  labelNames: ['org', 'method'],
});

export function setOrgsTotal(
  rows: { accountType: string; domain: string; count: number }[],
): void {
  orgsTotal.reset();
  for (const r of rows) {
    orgsTotal.set(
      {
        account_type: r.accountType === '' ? 'unknown' : r.accountType,
        domain: r.domain === '' ? 'unknown' : r.domain,
      },
      r.count,
    );
  }
}

export function setOrgAuthMethods(
  rows: { org: string; method: string }[],
): void {
  orgAuthMethods.reset();
  for (const r of rows) {
    orgAuthMethods.set({ org: r.org, method: r.method }, 1);
  }
}
