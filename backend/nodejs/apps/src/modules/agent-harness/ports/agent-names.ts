/**
 * The names of an org's agents, for an admin page that holds them by key (the
 * usage report, WP-51) and the budget notices. Read from the agents' own
 * records when asked, never stored with the spend. An agent that is not the
 * org's, or that nobody can name now, is absent from the answer.
 */
export interface AgentNameReader {
  names(
    orgId: string,
    agentKeys: readonly string[],
  ): Promise<Map<string, string>>;
}

/** What an admin list shows of an agent it holds by key (ADR-UI-03). */
export interface AgentSummary {
  name: string;
  /** The agent's latest published version; null while it has none or the answer did not say. */
  latestVersion: number | null;
}

/**
 * Names and latest published versions in one batched lookup per list
 * (ADR-UI-03), for a list whose rows pin agent versions (schedules). Only the
 * org's agents are in the answer; a failed lookup throws, and the caller shows
 * the rows without them.
 */
export interface AgentSummaryReader {
  summaries(
    orgId: string,
    agentKeys: readonly string[],
  ): Promise<Map<string, AgentSummary>>;
}
