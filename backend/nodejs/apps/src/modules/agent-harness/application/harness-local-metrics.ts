import { EXECUTION_STATUSES } from '../contracts/generated/ext/pipeshub/enums';
import { TERMINAL_RUN_STATUSES } from '../domain/execution';
import { keepFamilies, Scrape } from '../domain/metrics-exposition';
import {
  OPERATOR_SCOPE_KINDS,
  OperatorControl,
} from '../domain/operator-controls';
import type { RunStateReader, RunStateSnapshot } from '../ports/run-state';

export const RUN_STATE_METRICS = Object.freeze({
  runs: 'pipeshub_agent_harness_runs',
  activeSessions: 'pipeshub_agent_harness_active_sessions',
  leaseExpired: 'pipeshub_agent_harness_runs_lease_expired',
  oldestRunningAge: 'pipeshub_agent_harness_oldest_running_run_age_seconds',
  approvalsWaiting: 'pipeshub_agent_harness_approvals_waiting',
  oldestApprovalWait: 'pipeshub_agent_harness_approval_oldest_wait_seconds',
});

/** SEC-D-13: the operator controls in force by scope (ADR-OPS-01), 0 for a scope with none. */
export const KILL_SWITCH_METRIC = 'pipeshub_agent_harness_kill_switch';

/**
 * Node's own families the merged route passes on: the harness's and the chat paths' (DUR3), and the sign-in and API
 * credential checks (SEC-D-13), whose labels are fixed sets with no org.
 */
export const NODE_HARNESS_FAMILIES = [
  'pipeshub_agent_',
  'pipeshub_auth_attempts_',
] as const;

const ACTIVE_STATUSES = EXECUTION_STATUSES.filter(
  (status) => !TERMINAL_RUN_STATUSES.has(status),
);

const DEFAULT_TTL_MS = 15_000;

/** What the merged metrics route adds of Node's own (C9.04): scrapes it holds rather than fetches. */
export interface LocalMetrics {
  scrapes(): Promise<Scrape[]>;
}

export interface HarnessLocalMetricsOptions {
  /** Node's metrics registry, serialized (`metricsBackend.serialize`). */
  registry: () => Promise<string>;
  /** The run store's state; absent, no `run_store` scrape. */
  runs?: RunStateReader;
  /** The operator controls in force (null: no control service here); absent, no `operator_controls` scrape. */
  controls?: () => Promise<readonly OperatorControl[] | null>;
  clock?: { now(): number };
  /** How long one read of the run store answers every scrape. */
  ttlMs?: number;
}

function gauge(
  name: string,
  help: string,
  samples: Array<[string, number]>,
): string[] {
  return [
    `# HELP ${name} ${help}`,
    `# TYPE ${name} gauge`,
    ...samples.map(([labels, value]) => `${name}${labels} ${String(value)}`),
  ];
}

export function renderRunState(snapshot: RunStateSnapshot): string {
  return `${[
    ...gauge(
      RUN_STATE_METRICS.runs,
      'Durable runs in each status that is not terminal, read from the run store at scrape time',
      ACTIVE_STATUSES.map((status) => [
        `{status="${status}"}`,
        snapshot.byStatus[status] ?? 0,
      ]),
    ),
    ...gauge(
      RUN_STATE_METRICS.activeSessions,
      'Conversations with a durable root run that has not ended',
      [['', snapshot.activeSessions]],
    ),
    ...gauge(
      RUN_STATE_METRICS.leaseExpired,
      'Running durable runs whose lease expired (worker gone) and that the sweeper has not re-queued yet',
      [['', snapshot.leaseExpired]],
    ),
    ...gauge(
      RUN_STATE_METRICS.oldestRunningAge,
      'Seconds since the oldest running durable run was admitted; 0 when none runs',
      [['', snapshot.oldestRunningAgeS ?? 0]],
    ),
    ...gauge(
      RUN_STATE_METRICS.approvalsWaiting,
      'Durable runs waiting for a person to answer an approval (ADR-OPS-01 D10)',
      [['', snapshot.byStatus['waiting_approval'] ?? 0]],
    ),
    ...gauge(
      RUN_STATE_METRICS.oldestApprovalWait,
      'Seconds the longest-waiting run has waited for an approval; 0 when none waits',
      [['', snapshot.oldestApprovalWaitS ?? 0]],
    ),
  ].join('\n')}\n`;
}

export function renderKillSwitch(controls: readonly OperatorControl[]): string {
  const counts = new Map<string, number>();
  for (const control of controls) {
    counts.set(control.scope.kind, (counts.get(control.scope.kind) ?? 0) + 1);
  }
  return `${gauge(
    KILL_SWITCH_METRIC,
    'Operator stops in force by scope (ADR-OPS-01); the instance scope includes AGENT_HARNESS_KILL_SWITCH',
    OPERATOR_SCOPE_KINDS.map((scope) => [
      `{scope="${scope}"}`,
      counts.get(scope) ?? 0,
    ]),
  ).join('\n')}\n`;
}

/**
 * Node's part of the merged route: its harness families, and the run store's state read at scrape time. The store is
 * read at most once per `ttlMs` however many scrapers ask, and never on a request's path.
 */
export class HarnessLocalMetrics implements LocalMetrics {
  private readonly clock: { now(): number };
  private readonly ttlMs: number;
  private cached: { at: number; body: Promise<string | null> } | null = null;

  constructor(private readonly options: HarnessLocalMetricsOptions) {
    this.clock = options.clock ?? Date;
    this.ttlMs = options.ttlMs ?? DEFAULT_TTL_MS;
  }

  async scrapes(): Promise<Scrape[]> {
    const node = this.options
      .registry()
      .then((text) => keepFamilies(text, NODE_HARNESS_FAMILIES))
      .catch(() => null);
    const scrapes: Array<Promise<Scrape>> = [
      node.then((body) => ({ service: 'node', body })),
    ];
    if (this.options.runs !== undefined) {
      scrapes.push(
        this.runState().then((body) => ({ service: 'run_store', body })),
      );
    }
    const controls = this.options.controls;
    if (controls !== undefined) {
      scrapes.push(
        controls().then(
          (read) => ({
            service: 'operator_controls',
            body: read === null ? '' : renderKillSwitch(read),
          }),
          () => ({ service: 'operator_controls', body: null }),
        ),
      );
    }
    return Promise.all(scrapes);
  }

  private runState(): Promise<string | null> {
    const now = this.clock.now();
    if (this.cached === null || now - this.cached.at >= this.ttlMs) {
      const runs = this.options.runs as RunStateReader;
      const read = {
        at: now,
        body: runs.snapshot().then(renderRunState, () => {
          // A store that could not answer is asked again at the next scrape, not a TTL later.
          if (this.cached === read) this.cached = null;
          return null;
        }),
      };
      this.cached = read;
    }
    return this.cached.body;
  }
}
