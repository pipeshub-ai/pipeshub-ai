import { envFlag } from '../../libs/utils/env-flags';
import { hardenedProfile } from '../../libs/utils/security-profile';
import { offeringFromEnv } from './application/model-access-service';
import type { FlowSettings } from './domain/deployment-policy';
import { waitForPersonMs } from './domain/execution';
import { DEFAULT_CLAIM_FLOOR_EVERY } from './domain/priority-class';
export type AgentHarnessEnvironment = Readonly<
  Record<string, string | undefined>
>;

/** Whether the connected MongoDB can majority-acknowledge journal commits. */
export interface JournalCapability {
  durable: boolean;
  reason:
    | 'majority'
    | 'standalone'
    | 'arbiter'
    | 'majority_unavailable'
    | 'probe_failed'
    | 'preparation_failed'
    /** DOC_STORE=postgres: every commit is durable; the harness's stores are all on PostgreSQL. */
    | 'postgres'
    /** DOC_STORE=postgres, but some harness store has no PostgreSQL implementation yet. */
    | 'postgres_incomplete';
}

// Probed once at startup (prepareAgentHarnessStore); null until then.
let journalCapability: JournalCapability | null = null;

export const recordJournalCapability = (
  capability: JournalCapability | null,
): void => {
  journalCapability = capability;
};

export const currentJournalCapability = (): JournalCapability | null =>
  journalCapability;

/** Whether the probed capabilities are settled (ADR-CAP-01 D4); null while no capability service runs. */
let capabilitySettlement: (() => boolean) | null = null;

export const recordCapabilitySettlement = (
  settled: (() => boolean) | null,
): void => {
  capabilitySettlement = settled;
};

/**
 * Whether the document store has multi-document transactions: a MongoDB replica set (`REPLICA_SET_AVAILABLE`), or
 * PostgreSQL (`DOC_STORE=postgres`), where every unit of work is one.
 */
export const transactionsAvailable = (
  env: AgentHarnessEnvironment = process.env,
): boolean =>
  envFlag('REPLICA_SET_AVAILABLE', env) ||
  (env['DOC_STORE'] ?? '').trim().toLowerCase() === 'postgres';

/**
 * The agent harness's switch (ADR-OPS-06, amended 2026-11-20), `AGENT_HARNESS_WORKER_ENABLED`, on by default; each org
 * then turns it on in Labs (off by default). Off, the install serves none of the harness: no Labs flag, public route,
 * browser surface or background work of its own, and every chat takes the path a release without the harness takes.
 */
export const agentHarnessSwitchedOn = (
  env: AgentHarnessEnvironment = process.env,
): boolean => envFlag('AGENT_HARNESS_WORKER_ENABLED', env);

/**
 * Whether the agent harness is on for an org (ADR-OPS-08): the server switch on and the org's Labs
 * `ENABLE_AGENT_HARNESS` on. Everything a person or an org sees of the harness keys on it; only process and
 * infrastructure start-up keys on the switch alone. Infrastructure readiness, the kill switch and operator stops are
 * not part of it: they decide whether a new run is admitted, and never flip what a delete, an agent chat or the
 * composer does while an operator works on them. Python's twin is `is_agent_harness_on`.
 */
export type OrgHarnessReader = (orgId: string) => Promise<boolean>;

// Recorded once at composition (the rollout policy's `harnessOn`); null until then.
let orgHarnessReader: OrgHarnessReader | null = null;

export const recordOrgHarnessReader = (
  reader: OrgHarnessReader | null,
): void => {
  orgHarnessReader = reader;
};

// Recorded once at composition (Labs `ENABLE_SCHEDULED_RUNS`); null until then.
let orgSchedulesReader: OrgHarnessReader | null = null;

export const recordOrgSchedulesReader = (
  reader: OrgHarnessReader | null,
): void => {
  orgSchedulesReader = reader;
};

/**
 * Whether the org has scheduling (ADR-SCH-02 D1): Labs `ENABLE_SCHEDULED_RUNS` alone, on every install, whatever its
 * harness. `false` without an org, without a reader, or when the setting cannot be read (fail closed). Read where it
 * is used, never cached across a request.
 */
export async function orgSchedulesOn(
  orgId: string | null | undefined,
  reader: OrgHarnessReader | null = orgSchedulesReader,
): Promise<boolean> {
  if (!orgId || reader === null) return false;
  try {
    return await reader(orgId);
  } catch {
    return false;
  }
}

/**
 * `false` with the switch off, without an org, before a reader is recorded, or when the org's settings cannot be
 * read: the org then gets what a release without the harness does.
 */
export async function orgHarnessOn(
  orgId: string | null | undefined,
  env: AgentHarnessEnvironment = process.env,
): Promise<boolean> {
  if (!agentHarnessSwitchedOn(env) || !orgId || orgHarnessReader === null) {
    return false;
  }
  try {
    return await orgHarnessReader(orgId);
  } catch {
    return false;
  }
}

/** Told when an org's harness turns on from a Labs save; recorded by the harness's background services. */
export type OrgHarnessTurnedOn = (orgId: string) => void;

let orgHarnessTurnedOnListener: OrgHarnessTurnedOn | null = null;

export const recordOrgHarnessTurnedOn = (
  listener: OrgHarnessTurnedOn | null,
): void => {
  orgHarnessTurnedOnListener = listener;
};

/** Never throws: the Labs save that turned the harness on has landed whatever the listener does. */
export const orgHarnessTurnedOn = (orgId: string): void => {
  try {
    orgHarnessTurnedOnListener?.(orgId);
  } catch {
    // The listener's own work logs its failures; the save stays saved.
  }
};

/**
 * Whether any delete on this install may erase (the switch on, or the hardened profile, ADR-OPS-07): what the
 * erasure services and jobs are wired for at start-up. Each delete then asks `erasureOnDelete` for its org.
 */
export const erasureMayRun = (
  env: AgentHarnessEnvironment = process.env,
): boolean => agentHarnessSwitchedOn(env) || hardenedProfile(env);

/**
 * Whether deleting a chat, a person or an org of `orgId` erases its data (E7, ORGDEL, CLEANUP-G2) rather than only
 * marking it deleted: where the harness is on for the org, or the install runs the hardened profile. Otherwise a
 * delete is the soft delete a release without erasure did. Jobs already queued still finish.
 */
export const erasureOnDelete = async (
  orgId: string | null | undefined,
  env: AgentHarnessEnvironment = process.env,
): Promise<boolean> => hardenedProfile(env) || orgHarnessOn(orgId, env);

/**
 * Whether an org's delete can hand what no request can erase at once (its sessions, journals, data keys, audit chain)
 * to a durable erasure job: wherever a delete may erase (`erasureMayRun`) and the store has the transactions the job
 * needs. The retention sweeper that runs the job starts on every install.
 */
export const orgErasureJobsAvailable = (
  env: AgentHarnessEnvironment = process.env,
  journal: JournalCapability | null = journalCapability,
): boolean =>
  erasureMayRun(env) &&
  transactionsAvailable(env) &&
  journal?.durable !== false;

const environmentReady = (env: AgentHarnessEnvironment): boolean =>
  transactionsAvailable(env) &&
  envFlag('AGENT_HARNESS_RELAY_ENABLED', env) &&
  envFlag('AGENT_HARNESS_WORKER_ENABLED', env);

/**
 * Whether this deployment can safely admit transaction-backed durable runs.
 * A probed non-durable journal (standalone, PSA) keeps the harness off.
 */
export const isAgentHarnessInfrastructureReady = (
  env: AgentHarnessEnvironment = process.env,
  journal: JournalCapability | null = journalCapability,
): boolean => environmentReady(env) && journal?.durable !== false;

/**
 * `HARNESS_RECONCILE` (P2.9): unknown effects are first checked by a read of
 * the provider. Reported to the browser as information only: the console and
 * the chat card are served whatever it says (UX3).
 */
export const harnessReconcileEnabled = (
  env: AgentHarnessEnvironment = process.env,
): boolean => envFlag('HARNESS_RECONCILE', env);

/**
 * The deployment's flow settings (SEC1) as its environment sets them, read at
 * boot for the deployment layer of a pin. The worker reads the same three
 * variables; Compose and Helm set one environment for both.
 */
export const flowSettings = (
  env: AgentHarnessEnvironment = process.env,
): FlowSettings => ({
  guard: envFlag('HARNESS_FLOW_GUARD', env),
  level: env['HARNESS_FLOW_GUARD_LEVEL'],
  above: env['HARNESS_FLOW_GUARD_ABOVE'],
});

/**
 * `HARNESS_RUN_HISTORY_BY_REFERENCE` (PERF-B): admission writes a run's
 * history beside its document. Off only to roll back: a release before the
 * reference cannot claim a run that names one. Reading is never gated.
 */
export const runHistoryByReferenceEnabled = (
  env: AgentHarnessEnvironment = process.env,
): boolean => envFlag('HARNESS_RUN_HISTORY_BY_REFERENCE', env);

export const CLAIM_FLOOR_EVERY_ENV = 'AGENT_HARNESS_CLAIM_FLOOR_EVERY';

/**
 * `AGENT_HARNESS_CLAIM_FLOOR_EVERY` (G-45): every n-th claim-next of a Node process serves the oldest due run of
 * any class, the floor that keeps background and batch runs moving while interactive ones go first. 0: strict
 * priority. Unset, blank or not a whole number: the default.
 */
export const claimFloorEvery = (
  env: AgentHarnessEnvironment = process.env,
): number => {
  const raw = env[CLAIM_FLOOR_EVERY_ENV]?.trim();
  if (raw === undefined || !/^\d{1,6}$/.test(raw))
    return DEFAULT_CLAIM_FLOOR_EVERY;
  return Number(raw);
};

export interface AgentHarnessReadiness {
  ready: boolean;
  transactions: boolean;
  relay: boolean;
  worker: boolean;
  /** `durable: null` until the startup probe ran. */
  journal: { durable: boolean | null; reason: string | null };
}

export const describeAgentHarnessReadiness = (
  env: AgentHarnessEnvironment = process.env,
  journal: JournalCapability | null = journalCapability,
): AgentHarnessReadiness => ({
  ready: isAgentHarnessInfrastructureReady(env, journal),
  transactions: transactionsAvailable(env),
  relay: envFlag('AGENT_HARNESS_RELAY_ENABLED', env),
  worker: envFlag('AGENT_HARNESS_WORKER_ENABLED', env),
  journal: {
    durable: journal?.durable ?? null,
    reason: journal?.reason ?? null,
  },
});

/**
 * What an operator reads for each journal answer: the health route's `detail`
 * and the startup warning. `reason` stays the code to match on.
 */
export const JOURNAL_DETAIL: Readonly<
  Record<JournalCapability['reason'], string>
> = {
  majority:
    'MongoDB confirms writes on a majority of its data-bearing members.',
  standalone:
    'MongoDB runs as a single server, not a replica set, so durable agent runs cannot be stored safely. Chat uses the standard path. Run MongoDB as a replica set to turn them on.',
  arbiter:
    'MongoDB has an arbiter, so a write can be confirmed by one data member alone and lost on failover. Durable agent runs stay off and chat uses the standard path. Use three data-bearing members and no arbiter (on Helm: values-cloud.yaml or values-eks.yaml).',
  majority_unavailable:
    'MongoDB did not confirm a test write on a majority of its members. Durable agent runs stay off until it does: check that most replica-set members are up.',
  probe_failed:
    'PipesHub could not check whether MongoDB stores durable agent runs safely. Durable agent runs stay off; see the Node log.',
  preparation_failed:
    'PipesHub could not prepare the database for durable agent runs (see the Node log). Chat uses the standard path while it keeps trying.',
  postgres:
    'PostgreSQL stores every agent run durably: a commit is on disk before it is acknowledged.',
  postgres_incomplete:
    'Durable agent runs are not available yet on an install that keeps its documents in PostgreSQL (DOC_STORE=postgres): some of their stores exist only for MongoDB. Chat uses the standard path. Keep the documents in MongoDB (a replica set) to turn them on.',
};

export type AgentHarnessHealth =
  | { status: 'off' }
  | {
      status: 'available';
      /**
       * `settling` until every capability probe has answered (a broker still starting): a turn admitted then
       * waits for the answer instead of losing its sandbox (ADR-CAP-01 D4). What a lane waits on before it drives.
       */
      capabilities?: 'settled' | 'settling';
    }
  | {
      status: 'unavailable';
      reason: JournalCapability['reason'];
      detail: string;
    };

/**
 * The harness's part of the health route. `unavailable` is never the
 * application's health: the harness failing leaves every other route serving.
 */
export const agentHarnessHealth = (
  env: AgentHarnessEnvironment = process.env,
  journal: JournalCapability | null = journalCapability,
  settled: (() => boolean) | null = capabilitySettlement,
): AgentHarnessHealth => {
  if (!environmentReady(env)) return { status: 'off' };
  if (journal?.durable !== false) {
    return settled === null
      ? { status: 'available' }
      : {
          status: 'available',
          capabilities: settled() ? 'settled' : 'settling',
        };
  }
  return {
    status: 'unavailable',
    reason: journal.reason,
    detail: JOURNAL_DETAIL[journal.reason],
  };
};

/**
 * The harness settings a typo must stop at startup, before anything is served
 * (CPD-0, R2 CP0): the offering, which the model-access composition would
 * otherwise refuse only once the container is built, and how long a run waits
 * for a person (FIX-P1-F3), which a store would refuse only at a run's first
 * approval.
 */
export function validateAgentHarnessEnvironment(
  env: AgentHarnessEnvironment = process.env,
): void {
  offeringFromEnv(env);
  waitForPersonMs(env);
}
