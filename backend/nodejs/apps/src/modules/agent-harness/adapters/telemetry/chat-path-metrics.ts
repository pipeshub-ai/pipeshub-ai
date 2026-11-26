import { createHash } from 'crypto';
import { metricsBackend } from '../../../../libs/services/telemetry/metrics-backend';
import { ChatPathMetrics } from '../../ports/chat-path-metrics';

const turns = metricsBackend.createCounter({
  name: 'pipeshub_agent_chat_turns_total',
  help: "Chat turns ended, by path (legacy: today's loop; durable: the harness), surface and outcome",
  labelNames: ['path', 'surface', 'outcome'],
});

const duration = metricsBackend.createHistogram({
  name: 'pipeshub_agent_chat_turn_duration_seconds',
  help: "Seconds from a chat turn's start (legacy: the request; durable: admission) to its end, by path",
  labelNames: ['path', 'surface', 'outcome'],
  buckets: [1, 2.5, 5, 10, 20, 40, 80, 160, 320, 640, 1800],
});

const claims = metricsBackend.createCounter({
  name: 'pipeshub_agent_harness_run_claims_total',
  help: 'Durable runs claimed by a worker: queued (first claim) or expired_lease (a resume after a lost lease)',
  labelNames: ['from'],
});

const replays = metricsBackend.createCounter({
  name: 'pipeshub_agent_harness_commit_replays_total',
  help: 'Worker commands the store had already applied, sent again and answered from the receipt',
  labelNames: [],
});

const costUsd = metricsBackend.createCounter({
  name: 'pipeshub_agent_harness_model_cost_usd_total',
  help: "Metered USD of durable runs' model calls (budget ledger commits); divide by durable turns for cost per turn",
  labelNames: [],
});

const tokens = metricsBackend.createCounter({
  name: 'pipeshub_agent_harness_model_tokens_total',
  help: "Tokens of durable runs' model calls, as the budget ledger committed them",
  labelNames: [],
});

const claimWait = metricsBackend.createHistogram({
  name: 'pipeshub_agent_harness_run_claim_wait_seconds',
  help: 'Seconds from when a durable run could first be claimed (admission, or its notBefore) to its first claim',
  labelNames: [],
  buckets: [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 300],
});

const usageTokens = metricsBackend.createCounter({
  name: 'pipeshub_agent_harness_model_usage_tokens_total',
  help:
    "Tokens of durable runs' model calls by kind, as the budget ledger committed them; input counts every prompt " +
    'token, cache_read and cache_write are parts of it (cache ratio: cache_read / input)',
  labelNames: ['kind'],
});

const runCacheRead = metricsBackend.createHistogram({
  name: 'pipeshub_agent_harness_run_cache_read_ratio',
  help:
    "Each ended durable run's cache_read / input over its own model calls, as its budget account summed them " +
    '(SLO #11; runs that reported no prompt tokens are not counted)',
  labelNames: [],
  buckets: [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95],
});

const quarantined = metricsBackend.createCounter({
  name: 'pipeshub_agent_harness_quarantined_runs_total',
  help: 'Durable runs the worker quarantined after repeated failed claims (a crash loop)',
  labelNames: [],
});

const approvalsRequested = metricsBackend.createCounter({
  name: 'pipeshub_agent_harness_approvals_requested_total',
  help:
    'Approvals and questions durable runs asked people (interaction.requested), by org_bucket: one of 256 buckets ' +
    "of the org id's SHA-256, never the id",
  labelNames: ['org_bucket'],
});

/**
 * SEC-D-13 (ADR-OPS-03): a flood on one org stands out from the fleet's total, while the series stay at most 256 and
 * the merged route's no-tenant rule holds: a bucket is not an org id, and an operator finds an org's with this.
 */
export function orgBucket(orgId: string): string {
  return createHash('sha256').update(orgId).digest('hex').slice(0, 2);
}

export const prometheusChatPathMetrics: ChatPathMetrics = {
  turnEnded: (path, surface, outcome, durationS) => {
    turns.inc({ path, surface, outcome });
    duration.observe({ path, surface, outcome }, Math.max(0, durationS));
  },
  claimed: (from, firstClaimWaitS) => {
    claims.inc({ from });
    if (firstClaimWaitS !== undefined)
      claimWait.observe({}, Math.max(0, firstClaimWaitS));
  },
  commitReplayed: () => {
    replays.inc();
  },
  modelCharged: (usd, count, usage) => {
    if (usd > 0) costUsd.inc(undefined, usd);
    if (count > 0) tokens.inc(undefined, count);
    if (usage === undefined) return;
    const kinds: Array<[string, number]> = [
      ['input', usage.inputTokens],
      ['output', usage.outputTokens],
      ['cache_read', usage.cacheReadTokens],
      ['cache_write', usage.cacheWriteTokens],
    ];
    for (const [kind, value] of kinds)
      if (value > 0) usageTokens.inc({ kind }, value);
  },
  runCacheRead: (usage) => {
    if (usage.inputTokens <= 0) return;
    runCacheRead.observe(
      {},
      Math.min(1, Math.max(0, usage.cacheReadTokens / usage.inputTokens)),
    );
  },
  runQuarantined: () => {
    quarantined.inc();
  },
  approvalsRequested: (orgId, count) => {
    if (count > 0)
      approvalsRequested.inc({ org_bucket: orgBucket(orgId) }, count);
  },
};
