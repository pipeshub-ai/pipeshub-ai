/**
 * PostgreSQL implementation of the organisation metrics job (`DOC_STORE=postgres`). The periodic gauges read every
 * organisation, which only the jobs role may do: the process needs `PG_JOBS_URL`. Without it the reads answer nothing and the
 * gauges stay empty (one warning at the first call).
 */
import { Logger } from '../../services/logger.service';
import { system } from '../../context/orgContext';
import { withJobsUow, type Tx } from '../../db';
import type {
  AuthConfigMetricRow,
  OrgMetricRow,
  OrgMetricsJobRepository,
} from './types';

const logger = Logger.getInstance({ service: 'PgDocStore' });
let warned = false;

async function readAll<T>(fn: (tx: Tx) => Promise<T>, empty: T): Promise<T> {
  if (!process.env.PG_JOBS_URL) {
    if (!warned)
      logger.warn('PG_JOBS_URL is not set: the organisation gauges stay empty');
    warned = true;
    return empty;
  }
  return system('org-metrics', () => withJobsUow({}, fn));
}

export class PgOrgMetricsJobRepository implements OrgMetricsJobRepository {
  countUsersByOrg(): Promise<Array<{ _id: string | null; count: number }>> {
    return readAll(async (tx) => {
      const { rows } = await tx.query<{ org_id: string; n: string }>(
        '/* pipeshub:identity.metrics_users_by_org */ SELECT u.org_id, count(*) AS n FROM identity.users u WHERE NOT u.is_deleted GROUP BY u.org_id',
      );
      return rows.map((r) => ({ _id: r.org_id, count: Number(r.n) }));
    }, []);
  }

  listLiveOrgs(): Promise<OrgMetricRow[]> {
    return readAll(async (tx) => {
      const { rows } = await tx.query<{
        org_id: string;
        account_type: string;
        domain: string;
      }>(
        '/* pipeshub:identity.metrics_live_orgs */ SELECT o.org_id, o.account_type, o.domain FROM identity.orgs o WHERE NOT o.is_deleted',
      );
      return rows.map((r) => ({
        _id: r.org_id,
        accountType: r.account_type,
        domain: r.domain,
      }));
    }, []);
  }

  listLiveAuthConfigs(): Promise<AuthConfigMetricRow[]> {
    return readAll(async (tx) => {
      const { rows } = await tx.query<
        AuthConfigMetricRow & {
          org_id: string;
          auth_steps: AuthConfigMetricRow['authSteps'];
        }
      >(
        '/* pipeshub:identity.metrics_auth_configs */ SELECT c.org_id, c.auth_steps FROM identity.org_auth_config c WHERE NOT c.is_deleted',
      );
      return rows.map((r) => ({ orgId: r.org_id, authSteps: r.auth_steps }));
    }, []);
  }
}
