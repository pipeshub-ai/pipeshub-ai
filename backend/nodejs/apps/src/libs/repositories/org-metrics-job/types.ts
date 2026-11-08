/**
 * Org metrics job repository: cross-organisation reads for the periodic
 * gauges (organisations, users per organisation, sign-in methods). Bound only
 * in the job entry point; request code never reaches it.
 */
export interface OrgMetricRow {
  _id: string;
  accountType?: string | null;
  domain?: string | null;
}

export interface AuthConfigMetricRow {
  orgId: string;
  authSteps?: Array<{ allowedMethods?: Array<{ type?: unknown }> }>;
}

export interface OrgMetricsJobRepository {
  /** Live (not removed) accounts per organisation; `_id` is null for an account without one. */
  countUsersByOrg(): Promise<Array<{ _id: string | null; count: number }>>;
  listLiveOrgs(): Promise<OrgMetricRow[]>;
  listLiveAuthConfigs(): Promise<AuthConfigMetricRow[]>;
}
