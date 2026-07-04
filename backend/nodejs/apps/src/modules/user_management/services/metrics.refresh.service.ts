import { Org } from '../schema/org.schema';
import { Users } from '../schema/users.schema';
import { OrgAuthConfig } from '../../auth/schema/orgAuthConfiguration.schema';
import {
  setOrgsTotal,
  setOrgAuthMethods,
} from '../../../libs/services/telemetry/modules/org-metrics';
import { setOrgUsers } from '../../../libs/services/telemetry/modules/user-metrics';
import { normalizeOrgId } from '../../../libs/services/telemetry/identity';
import { Logger } from '../../../libs/services/logger.service';

const DEFAULT_INTERVAL_MS = 60000 * 5;

interface OrgRow {
  _id: unknown;
  accountType?: string | null;
  domain?: string | null;
}

interface AuthMethodLean {
  type?: unknown;
}

interface AuthStepLean {
  allowedMethods?: AuthMethodLean[];
}

interface AuthConfigLean {
  orgId: unknown;
  authSteps?: AuthStepLean[];
}

export function startOrgMetricsRefresh(
  logger: Logger,
  intervalMs: number = DEFAULT_INTERVAL_MS,
): NodeJS.Timeout {
  const refresh = async (): Promise<void> => {
    try {
      // Single org read drives both the per-account-type totals and the
      // org→domain lookup; grouping for the totals happens in Node.
      const [userAgg, orgRows] = await Promise.all([
        Users.aggregate<{ _id: string | null; count: number }>([
          { $match: { isDeleted: { $ne: true } } },
          { $group: { _id: '$orgId', count: { $sum: 1 } } },
        ]),
        Org.find(
          { isDeleted: { $ne: true } },
          { accountType: 1, domain: 1 },
        ).lean(),
      ]);
      const orgs = orgRows as OrgRow[];

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
      const authConfigs = (await OrgAuthConfig.find(
        { isDeleted: { $ne: true } },
        { orgId: 1, authSteps: 1 },
      ).lean()) as AuthConfigLean[];
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
  void refresh();
  const timer = setInterval(() => {
    void refresh();
  }, intervalMs);
  timer.unref();
  return timer;
}
