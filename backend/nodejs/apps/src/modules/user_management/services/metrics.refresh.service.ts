import { getOrgMetricsJobRepository } from '../../../libs/repositories/org-metrics-job';
import {
  setOrgsTotal,
  setOrgAuthMethods,
} from '../../../libs/services/telemetry/modules/org-metrics';
import { setOrgUsers } from '../../../libs/services/telemetry/modules/user-metrics';
import { normalizeOrgId } from '../../../libs/services/telemetry/identity';
import { spawnDetached, system } from '../../../libs/context/orgContext';
import { Logger } from '../../../libs/services/logger.service';

const DEFAULT_INTERVAL_MS = 60000 * 5;

export function startOrgMetricsRefresh(
  logger: Logger,
  intervalMs: number = DEFAULT_INTERVAL_MS,
): NodeJS.Timeout {
  const refresh = async (): Promise<void> => {
    try {
      // Single org read drives both the per-account-type totals and the
      // org→domain lookup; grouping for the totals happens in Node.
      const jobs = getOrgMetricsJobRepository();
      const [userAgg, orgs] = await Promise.all([
        jobs.countUsersByOrg(),
        jobs.listLiveOrgs(),
      ]);

      const norm = (v: string | null | undefined): string =>
        v == null || v === '' ? 'unknown' : v;

      const orgTotals = new Map<
        string,
        { accountType: string; domain: string; count: number }
      >();
      for (const o of orgs) {
        const accountType = norm(o.accountType);
        const domain = norm(o.domain);
        const key = `${accountType}\u0000${domain}`;
        const row = orgTotals.get(key);
        if (row) {
          row.count += 1;
        } else {
          orgTotals.set(key, { accountType, domain, count: 1 });
        }
      }
      setOrgsTotal([...orgTotals.values()]);

      const domainById = new Map<string, string>(
        orgs.map((o) => [normalizeOrgId(o._id), norm(o.domain)]),
      );
      setOrgUsers(
        userAgg.map((r) => {
          const orgId = normalizeOrgId(r._id);
          const domain = domainById.get(orgId);
          return {
            org: orgId,
            domain: domain == null || domain === '' ? 'unknown' : domain,
            count: r.count,
          };
        }),
      );

      // Enabled auth methods per org (presence only — never credentials).
      const authConfigs = await jobs.listLiveAuthConfigs();
      const authRows: { org: string; method: string }[] = [];
      for (const cfg of authConfigs) {
        const org = normalizeOrgId(cfg.orgId);
        // An auth config without an org is malformed — skip rather than
        // emit auth-method rows under an "unknown" org label.
        if (org === 'unknown') continue;
        const methods = new Set<string>();
        for (const step of cfg.authSteps ?? []) {
          for (const m of step.allowedMethods ?? []) {
            const methodType = m.type;
            if (typeof methodType === 'string' && methodType !== '') {
              methods.add(methodType);
            }
          }
        }
        for (const method of methods) {
          authRows.push({ org, method });
        }
      }
      setOrgAuthMethods(authRows);
    } catch (error) {
      logger.warn('Failed to refresh org metrics gauges', error);
    }
  };

  // Kick off immediately, then on an interval. unref so it never blocks exit.
  // Cross-org aggregation: explicit system scope, and a timer that keeps no caller context.
  const run = (): void => void system('metrics_refresh', refresh);
  const timer = spawnDetached(() => {
    run();
    return setInterval(run, intervalMs);
  });
  timer.unref();
  return timer;
}
