import { metricsBackend } from '../metrics-backend';

const orgUsers = metricsBackend.createGauge({
  name: 'pipeshub_org_users',
  help: 'Current number of (non-deleted) users per organization',
  labelNames: ['org'],
});

export function setOrgUsers(rows: { org: string; count: number }[]): void {
  orgUsers.reset();
  for (const r of rows) {
    orgUsers.set({ org: r.org }, r.count);
  }
}
