import type { ApprovalPolicySection } from './domain/approval-policy-sections';
import type { OrgApprovalRules } from './domain/approvals';
import type { RecordedRunField } from './domain/recorded-run-fields';
import type { Express, Router } from 'express';
import type { Container } from 'inversify';
import {
  indexLabel,
  type HarnessMigration,
} from '../../libs/utils/mongo-index-migration';
import { HARNESS_MIGRATIONS } from '../../libs/repositories/agent-harness/migrations/mongo';
import type { Tx } from '../../libs/repositories/uow';
import type { HarnessBinder } from './binders/context';
import type { CostEntryWriter } from './ports/cost-ledger';
import type { AuditAnchorSink } from './ports/audit';
import type { JournalAppendRecorder } from './ports/journal-append-recorder';
import type {
  OrgErasureParts,
  OrgErasureSteps,
  OrgPrincipalEraser,
} from './application/principal-erasure';
import type { StoreSession } from '../../libs/repositories/agent-harness/durable-transaction';
import type { AnyCpRouteSpec } from '../control-plane';
import type { KeyValueStoreService } from '../../libs/services/keyValueStore.service';
import type { OrgStopReader } from './ports/operator-stops';
import { agentHarnessSwitchedOn, type AgentHarnessEnvironment } from './config';
import type { AgentHarnessDependencies } from './composition';
import type { OrgLayers } from './adapters/control-plane/policy-snapshot-loader';

/** Adds routes to one of the harness's routers, after its built-in routes. */
export type HarnessRouteRegistrar = (
  router: Router,
  container: Container,
) => void;

/** A router an edition serves at a path of its own, beside the harness's built-in routers; each route checks its own caller. */
export interface HarnessRouterMount {
  readonly prefix: string;
  create(container: Container): Router;
}

/** Work an edition runs in the background of every Node process, beside the built-in sweepers. */
export interface HarnessBackgroundWorker {
  start(): void;
  /** Resolves once the worker's work in flight has ended. */
  stop(): Promise<void>;
}

/** Makes a worker from the bound harness, or null when what it needs is not bound here. */
export type HarnessWorkerFactory = (
  container: Container,
) => HarnessBackgroundWorker | null;

/**
 * Makes the source of an org's own policy layers (`org:<id>`, `agent:<key>`, `user:<id>` scopes) from the bound
 * harness; undefined when what it reads is not wired here.
 */
export type OrgPolicyLayersFactory = (
  container: Container,
  dependencies: AgentHarnessDependencies,
) => OrgLayers | undefined;

/** Makes one of a deleted org's erasure steps from the bound harness. */
export type HarnessOrgEraserFactory = (
  container: Container,
) => OrgPrincipalEraser;

/** Makes the steps an org's erasure job runs, from the bound harness and the parts any org erasure uses. */
export type HarnessOrgErasureFactory = (
  container: Container,
  parts: OrgErasureParts<StoreSession>,
) => OrgErasureSteps;

/**
 * What an edition adds to the agent harness: an extension point, supplied
 * through `harnessEdition` in src/config.ts. Every list extends the built-in
 * one and never replaces it; this build supplies `noopHarnessEdition`, which
 * adds nothing.
 */
export interface HarnessEdition {
  /**
   * Run before the built-in binders. Each built-in binder keeps a binding made
   * before it, so a token bound here is the one the whole harness uses; a
   * binder here reads built-in services lazily (`toDynamicValue`).
   */
  readonly binders: readonly HarnessBinder[];
  /** Added to the admin router (`/api/v1/agent-harness/admin`); each route checks its own caller. */
  readonly adminRoutes: readonly HarnessRouteRegistrar[];
  /** Added to the worker-only internal router; each route checks its own credential. */
  readonly internalRoutes: readonly HarnessRouteRegistrar[];
  /** Added to the agent configuration router (`/api/v1/agents`); each route checks its own caller. */
  readonly agentRoutes: readonly HarnessRouteRegistrar[];
  /** Index migrations run after the built-in ones, in this order; reverted before them. */
  readonly migrations: readonly HarnessMigration[];
  /**
   * Where the budget ledger writes each charged call's entry, inside the
   * transaction that commits its reservation. Called once, before any
   * binder runs, since the ledger is built then. Null: no entry is written.
   */
  readonly costEntries: (() => CostEntryWriter<Tx>) | null;
  /**
   * Where the audit chain's anchors are written. Called once, when the
   * journal is built. Null: the local append-only anchor store.
   */
  readonly auditAnchors: (() => AuditAnchorSink) | null;
  /**
   * What each journal append also records, in its transaction. Each is
   * called once, when the journal is built. None: nothing is recorded.
   */
  readonly journalRecorders: readonly (() => JournalAppendRecorder<StoreSession>)[];
  /** Started with the harness's background services, after the audit anchorer; stopped with them. */
  readonly workers: readonly HarnessWorkerFactory[];
  /** Erasers a deleted org's erasure runs after the built-in principal erasers (`OrgErasureParts.principals`). */
  readonly orgErasers: readonly HarnessOrgEraserFactory[];
  /** Mounted on the app after the harness's built-in routers. */
  readonly routers: readonly HarnessRouterMount[];
  /** Added to the control plane's admin route table (`/api/v1/control-plane`), after its built-in routes. */
  readonly controlPlaneRoutes: readonly AnyCpRouteSpec[];
  /**
   * The steps of an org's erasure job. Called once, when the retention
   * sweeper is built. Null: the built-in steps (`ImmediateOrgErasureSteps`).
   */
  readonly orgErasure: HarnessOrgErasureFactory | null;
  /**
   * This process's region (OWD-12), read when the harness is bound, before
   * any binder runs. Null: one region, `default`.
   */
  readonly region: (() => string) | null;
  /**
   * Run fields recorded from workers' events and handed to the model gateway
   * with the run's grant. Read when the harness is bound, before any binder
   * runs; their features are named to workers only then.
   */
  readonly recordedRunFields: readonly RecordedRunField[];
  /**
   * Run endings an edition's own rules end runs with, by code, with the
   * English a saved transcript shows when the run carries no words; this
   * build's own endings win. Read when the harness is bound.
   */
  readonly runFailureMessages: Readonly<Record<string, string>>;
  /**
   * Sections the edition adds to the org approval policy (`ApprovalPolicySection`): stored, migrated and saved
   * with the org's rules. Read when the harness is bound and by the policy-layers migration; what checks a saved
   * value is the edition's own.
   */
  readonly approvalPolicySections: readonly ApprovalPolicySection[];
  /**
   * An org's own policy layers, folded by every pin and by the live layer below the deployment's layers
   * (`orgLayers`). Null: none, so runs take the release's values narrowed by the deployment's layers alone.
   */
  readonly orgPolicyLayers: OrgPolicyLayersFactory | null;
  /**
   * Org and instance stops the rollout reads beyond the env switch
   * (ADR-OPS-01), from the deployment's settings store; each turn they stop
   * takes today's chat. Null: the env switch alone.
   */
  readonly rolloutStops:
    ((settings: KeyValueStoreService) => OrgStopReader) | null;
  /**
   * Rule layers the edition keeps in the approvals document beside the deployment's, read wherever Node reads the
   * document as the worker does (Python's `edition_config.org_approval_rules`). Null: the deployment's rules alone.
   */
  readonly orgApprovalRules: OrgApprovalRules | null;
}

export const noopHarnessEdition: HarnessEdition = Object.freeze({
  binders: [],
  adminRoutes: [],
  internalRoutes: [],
  agentRoutes: [],
  migrations: [],
  costEntries: null,
  auditAnchors: null,
  journalRecorders: [],
  workers: [],
  orgErasers: [],
  routers: [],
  controlPlaneRoutes: [],
  orgErasure: null,
  region: null,
  recordedRunFields: [],
  runFailureMessages: Object.freeze({}),
  approvalPolicySections: [],
  orgPolicyLayers: null,
  rolloutStops: null,
  orgApprovalRules: null,
});

/** The container token `bindAgentHarness` binds the edition to, for the routers. */
export const AGENT_HARNESS_EDITION = 'AgentHarnessEdition';

export const harnessEditionOf = (container: Container): HarnessEdition =>
  container.isBound(AGENT_HARNESS_EDITION)
    ? container.get<HarnessEdition>(AGENT_HARNESS_EDITION)
    : noopHarnessEdition;

export const mountEditionRoutes = (
  router: Router,
  container: Container,
  registrars: readonly HarnessRouteRegistrar[],
): void => {
  for (const register of registrars) register(router, container);
};

/**
 * Mounts each of the edition's routers at its prefix, made from the bound
 * harness. A prefix outside `/api/`, or one two routers share, is refused at
 * boot: either would shadow a route of the app. The routers serve the edition's
 * harness features, so with the harness switched off (ADR-OPS-06) none is made
 * or mounted, wherever its prefix is.
 */
export function mountEditionRouters(
  app: Pick<Express, 'use'>,
  container: Container,
  edition: HarnessEdition,
  env: AgentHarnessEnvironment = process.env,
): void {
  const prefixes = new Set<string>();
  for (const mount of edition.routers) {
    if (!mount.prefix.startsWith('/api/')) {
      throw new Error(
        `An edition router's prefix ${mount.prefix} is not under /api/`,
      );
    }
    if (prefixes.has(mount.prefix)) {
      throw new Error(`Two edition routers are mounted at ${mount.prefix}`);
    }
    prefixes.add(mount.prefix);
  }
  if (!agentHarnessSwitchedOn(env)) return;
  for (const mount of edition.routers) {
    app.use(mount.prefix, mount.create(container));
  }
}

const running = new WeakMap<Container, HarnessBackgroundWorker[]>();

/** Starts the edition's workers once per container. */
export function startEditionWorkers(container: Container): void {
  if (running.has(container)) return;
  const workers: HarnessBackgroundWorker[] = [];
  for (const make of harnessEditionOf(container).workers) {
    const worker = make(container);
    if (worker === null) continue;
    worker.start();
    workers.push(worker);
  }
  running.set(container, workers);
}

/** Stops what `startEditionWorkers` started; one worker failing to stop does not keep the others running. */
export async function stopEditionWorkers(container: Container): Promise<void> {
  const workers = running.get(container);
  running.delete(container);
  if (workers === undefined) return;
  await Promise.allSettled(workers.map((worker) => worker.stop()));
}

export const editionOrgErasers = (container: Container): OrgPrincipalEraser[] =>
  harnessEditionOf(container).orgErasers.map((make) => make(container));

/**
 * The built-in migrations, then the edition's. An id that repeats, or an
 * index two migrations both build, would let one migration's `down` drop
 * what the other still needs: refused at boot, as C-15 refuses it among the
 * built-in ones.
 */
export function harnessMigrations(
  edition: HarnessEdition,
  builtIn: readonly HarnessMigration[] = HARNESS_MIGRATIONS,
): readonly HarnessMigration[] {
  const all = [...builtIn, ...edition.migrations];
  const ids = new Set<string>();
  const indexes = new Map<string, string>();
  for (const migration of all) {
    if (ids.has(migration.id)) {
      throw new Error(
        `Harness migration id "${migration.id}" is registered twice`,
      );
    }
    ids.add(migration.id);
    for (const index of migration.indexes) {
      const label = indexLabel(index);
      const owner = indexes.get(label);
      if (owner !== undefined) {
        throw new Error(
          `Harness index ${label} is built by both "${owner}" and "${migration.id}"`,
        );
      }
      indexes.set(label, migration.id);
    }
  }
  return all;
}
