import { metricsBackend } from '../metrics-backend';

const orgUsers = metricsBackend.createGauge({
  name: 'pipeshub_org_users',
  help: 'Current number of (non-deleted) users per organization',
  labelNames: ['org', 'domain'],
});

export function setOrgUsers(
  rows: { org: string; domain: string; count: number }[],
): void {
  orgUsers.reset();
  for (const r of rows) {
    orgUsers.set(
      { org: r.org, domain: r.domain === '' ? 'unknown' : r.domain },
      r.count,
    );
  }
}
