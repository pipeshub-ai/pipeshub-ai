/**
 * The Node journal over HTTP for the Python journal client's contract suite
 * (P1a S5, D-12): the real internal router, controller, ExecutionService,
 * JournalService, attempt-token authority and error middleware over the
 * in-memory store, plus `/__control/*` for the Node-side operations the
 * suite drives (admit, DB time, sweep, cancel, resolve, reset; for sealed
 * content: shred, tamper-content, content-bytes; for workspaces (P3.2):
 * shred-workspace-key; for gateway tokens (E2/E3, C-13): gateway-public-keys,
 * requests; for the budget ledger (P6.5): budget/run, budget/open-root, budget/charge,
 * budget/open-child, budget/close, budget/token; for schedules (P8a): schedule,
 * schedule-tick, which fire through the real ScheduleFirer and admission
 * service). `/admit` takes an optional
 * `orgId` so a suite can hold runs of two tenants. `JOURNAL_HTTP_HOST` picks the
 * listen address (default 127.0.0.1), for a suite whose containers must reach this server.
 *
 *   TS_NODE_PROJECT=tsconfig.test.json npx ts-node --transpile-only \
 *     tests/modules/agent-harness/support/journal-http-server.ts
 *
 * Prints `READY <port> <worker credential> <egress proxy credential>` once listening: a gateway token is minted only
 * with the egress proxy's (ADR-CRED-01).
 */
import 'reflect-metadata';
import express, { Request, Response } from 'express';
import { AddressInfo } from 'node:net';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { Container } from 'inversify';
import { InMemoryExecutionStore } from '../../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { InMemoryConfigDocumentStore } from '../../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { ExecutionService } from '../../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { ChildRunService } from '../../../../src/modules/agent-harness/application/child-run-service';
import { engineCarry } from '../../../../src/modules/agent-harness/application/engine-carry';
import { testModelAccess } from './model-access';
import { RunJournalReader } from '../../../../src/modules/agent-harness/application/run-journal-reader';
import { InteractionService } from '../../../../src/modules/agent-harness/application/interaction-service';
import { HarnessConflictError } from '../../../../src/modules/agent-harness/domain/errors';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_BUDGET_LEDGER,
  AGENT_HARNESS_CHILD_RUNS,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_GATEWAY_TOKENS,
  AGENT_HARNESS_MEMORIES,
  AGENT_HARNESS_RUN_CONTENT,
  AGENT_HARNESS_WORKSPACES,
  createHarnessAuthority,
} from '../../../../src/modules/agent-harness/composition';
import { RunContentService } from '../../../../src/modules/agent-harness/application/run-content-service';
import { GatewayTokenAuthority } from '../../../../src/modules/agent-harness/application/gateway-token-authority';
import { GatewayTokenService } from '../../../../src/modules/agent-harness/application/gateway-token-service';
import {
  InMemoryWorkspaceKeyStore,
  InMemoryWorkspaceRepository,
  InMemoryWorkspaceWriterStore,
} from '../../../../src/modules/agent-harness/adapters/in-memory-workspace-store';
import { SessionWorkspaceKeys } from '../../../../src/modules/agent-harness/application/session-workspace-keys';
import { WorkspaceService } from '../../../../src/modules/agent-harness/application/workspace-service';
import { canonicalDigest } from '../../../../src/modules/agent-harness/contracts/canonical-json';
import { RunSpec } from '../../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  capabilitySnapshot,
  LEAST_CAPABILITIES,
} from '../../../../src/modules/agent-harness/domain/capabilities';
import {
  PinnedRunSpec,
  principalRefFor,
  RunSpecResolver,
} from '../../../../src/modules/agent-harness/domain/run-spec';
import { InMemoryBudgetLedger } from '../../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { BudgetLedgerService } from '../../../../src/modules/agent-harness/application/budget-ledger-service';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { BudgetAmount, BudgetCap, BudgetUsage } from '../../../../src/modules/agent-harness/domain/budget-ledger';
import { createAgentHarnessInternalRouter } from '../../../../src/modules/agent-harness/transport/internal-routes';
import { InMemoryMemoryStore } from '../../../../src/modules/agent-harness/adapters/in-memory-memory-store';
import { MemoryService } from '../../../../src/modules/agent-harness/application/memory-service';
import { SharedCitationService } from '../../../../src/modules/agent-harness/application/shared-citations';
import { MEMORY_OFF, type MemoryEntry, type MemoryPolicy } from '../../../../src/modules/agent-harness/domain/memory';
import { ErrorMiddleware } from '../../../../src/libs/middlewares/error.middleware';
import { dataKeyRing, testCipher } from './data-protection';
import { InMemoryScheduleStore } from '../../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { RunAdmissionService } from '../../../../src/modules/agent-harness/application/run-admission-service';
import { ScheduleFirer } from '../../../../src/modules/agent-harness/application/schedule-firer';
import { ScheduleService } from '../../../../src/modules/agent-harness/application/schedule-service';
import { RunAdmissionScheduledRuns } from '../../../../src/modules/agent-harness/application/scheduled-run-admission';
import mongoose from 'mongoose';
import { connectMongoWorld, fileStores, MongoWorld } from './journal-mongo-world';
import { Logger } from '../../../../src/libs/services/logger.service';
import { OutboxDispatcher } from '../../../../src/libs/services/outbox/outbox.dispatcher';
import {
  buildRedisBrokerConfig,
  createMessageProducer,
  ensureMessageTopicsExist,
  ResolvedMessageBrokerConfig,
} from '../../../../src/libs/services/message-broker.factory';
import { MessageBrokerType } from '../../../../src/libs/types/messaging.types';
import { testControlPlane } from '../../control-plane/support/in-memory';
import { testBindingKeys } from './binding-proof';

// Mongo mode (P9): the production Mongo adapters, so a lane can restart this process or restore its database.
const MONGO_URI = process.env['JOURNAL_MONGO_URI'];
const MONGO_MODE = MONGO_URI !== undefined && MONGO_URI !== '';
// Mongo admission takes object ids, as the product's do.
const ORG = MONGO_MODE ? '66f6a6a6a6a6a6a6a6a6a6a1' : 'org-contract';
const ACTOR = MONGO_MODE ? '66f6a6a6a6a6a6a6a6a6b001' : 'actor-contract';

/** A query parameter as the routes read it; an absent one reads as `undefined`, as String() spelt it. */
const queryText = (value: unknown): string => (typeof value === 'string' ? value : (JSON.stringify(value) ?? 'undefined'));

interface World {
  now: number;
  store: InMemoryExecutionStore;
  service: ExecutionService;
  children: ChildRunService;
  specs: Map<string, RunSpec>;
  keys: ReturnType<typeof dataKeyRing>;
  contentBlobs: InMemoryPayloadStore;
  journals: JournalService;
  content: RunContentService;
  workspaceKeys: SessionWorkspaceKeys;
  workspaces: WorkspaceService;
  budgets: InMemoryBudgetLedger;
  /**
   * Runs the budget routes see as leased (P6.5), by `BUDGET_ATTEMPT` unless a worker's claimed attempt is named
   * (QA-19: `AttemptBudgetAccounts` calls with that attempt's token); a pin opens one lazily.
   */
  budgetRuns: Map<
    string,
    {
      parentRunId: string | null;
      pin: { maxCostUsd: number; maxTokens: number } | null;
      attempt?: { attemptId: string; fence: number };
    }
  >;
  /** Schedules (P8a) and the spec each one's runs pin; their clock may run ahead of `now` for one tick. */
  schedules: InMemoryScheduleStore;
  scheduleSpecs: Map<string, RunSpec>;
  scheduleNow: number | null;
  admissions: RunAdmissionService;
  memoryStore: InMemoryMemoryStore;
  memoryPolicy: MemoryPolicy;
  memories: MemoryService;
}

const BUDGET_ATTEMPT = 'budget-attempt';
/** A valid pin (the shared vector); a budget run pins its own budgets in it. */
const PINNED_VECTOR = (
  JSON.parse(
    readFileSync(
      join(__dirname, '../../../../../../../contracts/harness/vectors/pinned-run-spec.json'),
      'utf-8',
    ),
  ) as { pinned: PinnedRunSpec }
).pinned;
// P6.5's ledger suites issue and expire tokens on the world clock; C-13's route authority below uses wall time.
const budgetGatewayTokens = new GatewayTokenAuthority({
  signingKeys: new InMemoryConfigDocumentStore(),
  publishedKeys: new InMemoryConfigDocumentStore(),
  clock: { now: () => world.now },
});

/** The budget service's view of a run: leased by `BUDGET_ATTEMPT`, its pin if one was registered. */
const budgetRun = (
  orgId: string,
  runId: string,
): { run: HarnessRun; pinned: PinnedRunSpec | null } | null => {
  const known = world.budgetRuns.get(runId);
  if (known === undefined) return null;
  const run = {
    orgId,
    runId,
    sessionId: `budget-${runId}`,
    status: 'running',
    attemptId: known.attempt?.attemptId ?? BUDGET_ATTEMPT,
    fence: known.attempt?.fence ?? 1,
    leaseExpiresAt: world.now + 3_600_000,
    ...(known.parentRunId !== null
      ? { parent: { runId: known.parentRunId, invocationId: runId, depth: 1 } }
      : {}),
  } as unknown as HarnessRun;
  const pinned =
    known.pin === null
      ? null
      : ({
          ...PINNED_VECTOR,
          spec: {
            ...PINNED_VECTOR.spec,
            budgets: { ...PINNED_VECTOR.spec.budgets, ...known.pin },
          },
        } as PinnedRunSpec);
  return { run, pinned };
};

const newWorld = (): World => {
  const world = {
    now: Date.UTC(2026, 8, 26, 12),
    specs: new Map<string, RunSpec>(),
  } as World;
  const payloads = new InMemoryPayloadStore();
  const journals = new JournalService(payloads, testCipher());
  world.journals = journals;
  world.store = new InMemoryExecutionStore(
    { now: () => world.now },
    undefined,
    undefined,
    null,
    journals,
  );
  const resolve: RunSpecResolver = (subject, capabilities) => {
    const spec = world.specs.get(subject.run.runId);
    if (spec === undefined) throw new Error('no spec admitted for this run');
    return {
      spec,
      specDigest: canonicalDigest(spec),
      capabilities: { ...capabilities.capabilities },
      capabilitiesDigest: capabilities.digest,
    };
  };
  world.service = new ExecutionService({
    store: world.store,
    journal: world.store.journal,
    journals,
    capabilities: {
      current: () =>
        capabilitySnapshot(
          { ...LEAST_CAPABILITIES, 'journal.durable': true },
          world.now,
        ),
    },
    resolveSpec: resolve,
    children: world.store,
    budgets: { account: (orgId, runId) => world.budgets.account(orgId, runId) },
  });
  world.keys = dataKeyRing();
  world.contentBlobs = new InMemoryPayloadStore();
  world.content = new RunContentService(
    world.service,
    world.keys.cipher,
    world.contentBlobs,
  );
  world.workspaceKeys = new SessionWorkspaceKeys({
    store: new InMemoryWorkspaceKeyStore(),
    cipher: world.keys.cipher,
    now: () => world.now,
  });
  world.workspaces = new WorkspaceService({
    runs: world.service,
    repository: new InMemoryWorkspaceRepository(),
    cipher: world.keys.cipher,
    keys: world.workspaceKeys,
    writers: new InMemoryWorkspaceWriterStore(),
    now: () => world.now,
  });
  world.children = new ChildRunService({
    runs: world.store,
    children: world.store,
    journals,
    workspaces: world.workspaces,
    // A code_task child (CTL-6) is decided as admission decides a coder agent's engine run.
    engineAccess: (subject) => testModelAccess().decide(subject),
    engineCarry: engineCarry({
      runs: world.store,
      journal: new RunJournalReader({ journal: world.store.journal, journals }),
    }),
  });
  world.budgets = new InMemoryBudgetLedger(() => world.now);
  world.budgetRuns = new Map();
  world.schedules = new InMemoryScheduleStore({
    now: () => world.scheduleNow ?? world.now,
  });
  world.scheduleSpecs = new Map();
  world.scheduleNow = null;
  const v2 = { useHarness: true, harnessMajor: 2 as const, reason: 'enabled' as const };
  world.admissions = new RunAdmissionService({
    store: world.store,
    rollout: {
      decide: async () => v2,
      decideForOrg: async () => ({ ...v2, harnessOn: true, resumeInFlightRuns: true }),
    },
    clock: { now: () => world.now },
  });
  // P7.3: governed memory as Node serves it to workers (no record authority: recall is pinned).
  world.memoryStore = new InMemoryMemoryStore();
  world.memoryPolicy = MEMORY_OFF;
  world.memories = new MemoryService({
    store: world.memoryStore,
    settings: { read: async () => world.memoryPolicy },
    access: new SharedCitationService(null),
    runs: world.store,
    now: () => world.now,
  });
  return world;
};

/** The actor as the schedule's authority reads them: always active, never an admin. */
const scheduleActors = {
  authorize: async (_orgId: string, actorId: string) => ({
    authorization: `Bearer token-of-${actorId}`,
  }),
  isOrgAdmin: async () => false,
};
const scheduledRunsOn = { enabled: async () => true };

/** A firer over the world's schedules whose admitted runs pin their schedule's spec. */
const newFirer = (): ScheduleFirer =>
  new ScheduleFirer({
    store: world.schedules,
    admission: {
      admit: async (request) => {
        const outcome = await new RunAdmissionScheduledRuns(
          world.admissions,
        ).admit(request);
        const spec = world.scheduleSpecs.get(request.trigger.scheduleId);
        if (outcome.kind === 'admitted' && spec !== undefined) {
          world.specs.set(outcome.runId, spec);
        }
        return outcome;
      },
    },
    actors: scheduleActors,
    setting: scheduledRunsOn,
  });
let mongoWorld: MongoWorld | null = null;

/** The same services over Mongo; a reset keeps them (each lane uses a database of its own). */
const newMongoWorld = (m: MongoWorld): World => {
  const resolve: RunSpecResolver = (subject, capabilities) => {
    const spec = m.specs.get(subject.run.runId);
    if (spec === undefined) throw new Error('no spec admitted for this run');
    return {
      spec,
      specDigest: canonicalDigest(spec),
      capabilities: { ...capabilities.capabilities },
      capabilitiesDigest: capabilities.digest,
    };
  };
  const mongoWorldState = {
    specs: m.specs,
    keys: m.keys,
    journals: m.journals,
    contentBlobs: new InMemoryPayloadStore(),
    workspaceKeys: m.workspaceKeys,
    budgets: new InMemoryBudgetLedger(() => Date.now()),
    budgetRuns: new Map(),
    store: {
      admit: (command: never, now: number) => m.admission.admit(command, now),
      journal: m.journal,
      sessionOwners: new Map(),
    },
  } as unknown as World;
  Object.defineProperty(mongoWorldState, 'now', { get: () => Date.now(), set: () => undefined });
  mongoWorldState.service = new ExecutionService({
    store: m.executionStore,
    journal: m.journal,
    journals: m.journals,
    history: m.history,
    inputs: m.inputs,
    capabilities: {
      current: () =>
        capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, Date.now()),
    },
    resolveSpec: resolve,
    children: m.childRuns,
  });
  mongoWorldState.content = m.contentFor(mongoWorldState.service);
  mongoWorldState.workspaces = m.workspacesFor(mongoWorldState.service);
  mongoWorldState.children = m.childrenFor(mongoWorldState.workspaces);
  return mongoWorldState;
};

let world = newWorld();

/** Every call reaches the current world, so a reset needs no new router. */
const executions = new Proxy({} as ExecutionService, {
  get: (_target, property) => {
    const value = (world.service as unknown as Record<PropertyKey, unknown>)[
      property
    ];
    return typeof value === 'function' ? value.bind(world.service) : value;
  },
});

const content = new Proxy({} as RunContentService, {
  get: (_target, property) => {
    const value = (world.content as unknown as Record<PropertyKey, unknown>)[
      property
    ];
    return typeof value === 'function' ? value.bind(world.content) : value;
  },
});

const children = new Proxy({} as ChildRunService, {
  get: (_target, property) => {
    const value = (world.children as unknown as Record<PropertyKey, unknown>)[
      property
    ];
    return typeof value === 'function' ? value.bind(world.children) : value;
  },
});

let budgetService: BudgetLedgerService | null = null;
const budgets = new Proxy({} as BudgetLedgerService, {
  get: (_target, property) => {
    const value = (budgetService as unknown as Record<PropertyKey, unknown>)[
      property
    ];
    return typeof value === 'function' ? value.bind(budgetService) : value;
  },
});

const memories = new Proxy({} as MemoryService, {
  get: (_target, property) => {
    // The Mongo world (P9 lanes) serves no memories; inversify probes `then` when it binds this.
    if (world.memories === undefined) return undefined;
    const value = (world.memories as unknown as Record<PropertyKey, unknown>)[
      property
    ];
    return typeof value === 'function' ? value.bind(world.memories) : value;
  },
});

/**
 * What a suite adds to this server through `JOURNAL_SERVER_EXTENSION` (a module path whose `extend` export is
 * called once, before the routers are built): bindings the internal router reads, control routes under
 * `/__control`, and state to drop on `/__control/reset`.
 */
export interface JournalServerExtensionContext {
  readonly container: Container;
  readonly control: express.Router;
  readonly orgId: string;
  now(): number;
  readonly workspaceKeys: SessionWorkspaceKeys;
  onReset(reset: () => void): void;
}
export type JournalServerExtension = (context: JournalServerExtensionContext) => void | Promise<void>;

const forward = <T extends object>(pick: () => T | undefined): T =>
  new Proxy({} as T, {
    get: (_target, property) => {
      const target = pick();
      // A world without the service (the Mongo world of the P9 lanes); inversify probes `then` when it binds this,
      // so a missing service reads as undefined rather than throwing at startup.
      if (target === undefined) return undefined;
      const value = (target as unknown as Record<PropertyKey, unknown>)[property];
      return typeof value === 'function' ? value.bind(target) : value;
    },
  });

const workspaces = new Proxy({} as WorkspaceService, {
  get: (_target, property) => {
    const value = (world.workspaces as unknown as Record<PropertyKey, unknown>)[
      property
    ];
    return typeof value === 'function' ? value.bind(world.workspaces) : value;
  },
});

async function main(): Promise<void> {
  if (MONGO_MODE) {
    mongoWorld = await connectMongoWorld(MONGO_URI as string, process.env['JOURNAL_MONGO_DB'] ?? 'pxh_journal_server');
    world = newMongoWorld(mongoWorld);
  }
  const state = MONGO_MODE ? fileStores(process.env['JOURNAL_STATE_DIR'] ?? '.journal-server-state') : null;
  const documents = <T>(name: string) =>
    state !== null ? state.at<T>(name) : new InMemoryConfigDocumentStore<T>();
  const stores = {
    workerCredential: documents<never>('worker-credential'),
    attemptSigningKeys: documents<never>('attempt-signing-keys'),
    attemptPublicKeys: documents<never>('attempt-public-keys'),
    roleCredentials: {
      scrape: documents<never>('scrape-credential'),
      'egress-proxy': documents<never>('egress-proxy-credential'),
      'sandbox-broker': documents<never>('sandbox-broker-credential'),
    },
  };
  const authority = createHarnessAuthority(stores as never, executions);
  await authority.provision(null);
  const credential = await authority.workerCredential();
  const proxyCredential = await authority.credentialFor('egress-proxy');
  const gatewayPublicKeys = documents<never>('gateway-public-keys');
  // Leases are judged on the world's clock, as the execution store sets them.
  const gatewayTokens = new GatewayTokenService({
    tokens: new GatewayTokenAuthority({
      signingKeys: documents<never>('gateway-signing-keys'),
      publishedKeys: gatewayPublicKeys,
    }),
    runs: executions,
    clock: { now: () => world.now },
    // Python's contract tests sign their bindings under the same test key (R38IDENT-FU-7).
    bindingKeys: testBindingKeys,
  });
  await gatewayTokens.provision(null);
  const requests: string[] = [];

  const container = new Container();
  container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue(executions);
  container.bind(AGENT_HARNESS_AUTHORITY).toConstantValue(authority);
  container.bind(AGENT_HARNESS_RUN_CONTENT).toConstantValue(content);
  container.bind(AGENT_HARNESS_WORKSPACES).toConstantValue(workspaces);
  container.bind(AGENT_HARNESS_CHILD_RUNS).toConstantValue(children);
  container.bind(AGENT_HARNESS_GATEWAY_TOKENS).toConstantValue(gatewayTokens);
  container.bind(AGENT_HARNESS_BUDGET_LEDGER).toConstantValue(budgets);
  container.bind(AGENT_HARNESS_MEMORIES).toConstantValue(memories);
  const newBudgetService = (): BudgetLedgerService =>
    new BudgetLedgerService({
      ledger: {
        account: (o, r) => world.budgets.account(o, r),
        ensure: (o, c, op) => world.budgets.ensure(o, c, op),
        openRoot: (o, r, c, op) => world.budgets.openRoot(o, r, c, op),
        openChild: (o, p, c, cap) => world.budgets.openChild(o, p, c, cap),
        reserve: (command) => world.budgets.reserve(command),
        commit: (o, r, k, a, u, c, h) => world.budgets.commit(o, r, k, a, u, c, h),
        reconcile: (o, r, k, a, u, c) => world.budgets.reconcile(o, r, k, a, u, c),
        held: (o, holder) => world.budgets.held(o, holder),
        release: (o, r, k) => world.budgets.release(o, r, k),
        remaining: (o, r) => world.budgets.remaining(o, r),
        close: (o, r) => world.budgets.close(o, r),
      },
      // A run the suite registered for the budget routes, else an admitted run as production resolves it (its
      // lease and pinned spec): a worker's own metered calls (an image, a helper model) reserve on that account.
      runs: {
        pinned: async (orgId, runId) =>
          budgetRun(orgId, runId) ?? (await executions.pinned(orgId, runId)),
      },
      attempts: authority,
      gateways: budgetGatewayTokens,
      clock: { now: () => world.now },
    });
  budgetService = newBudgetService();

  const extensionControl = express.Router();
  const resets: Array<() => void> = [];
  const extensionPath = process.env['JOURNAL_SERVER_EXTENSION'];
  if (extensionPath !== undefined && extensionPath !== '') {
    const loaded = (await import(extensionPath)) as { extend?: JournalServerExtension };
    if (typeof loaded.extend !== 'function') {
      throw new Error(`${extensionPath} exports no extend function`);
    }
    await loaded.extend({
      container,
      control: extensionControl,
      orgId: ORG,
      now: () => world.now,
      workspaceKeys: forward(() => world.workspaceKeys),
      onReset: (reset) => resets.push(reset),
    });
  }

  const app = express();
  app.use('/api', (req, _res, next) => {
    requests.push(`${req.method} ${req.originalUrl}`);
    next();
  });
  app.use(express.json({ limit: '10mb' }));
  app.use(
    '/api/v1/agent-harness/internal',
    createAgentHarnessInternalRouter(container),
  );

  const control = express.Router();
  const handle =
    (work: (req: Request) => Promise<unknown>) =>
    (req: Request, res: Response): void => {
      work(req).then(
        (body) => res.status(200).json(body ?? {}),
        (error: unknown) =>
          error instanceof HarnessConflictError
            ? res.status(409).json({ error: error.message, code: error.code })
            : res.status(500).json({
                error: error instanceof Error ? error.message : String(error),
              }),
      );
    };

  control.post(
    '/reset',
    handle(async () => {
      if (mongoWorld === null) world = newWorld();
      for (const reset of resets) reset();
      requests.length = 0;
    }),
  );
  control.post(
    '/admit',
    handle(async (req) => {
      const body = req.body as {
        runId: string;
        sessionId: string;
        streamId?: string;
        inputText: string;
        spec: RunSpec;
        orgId?: string;
        inputArtifacts?: Array<{ artifactId: string; version?: number }>;
      };
      const orgId = body.orgId ?? ORG;
      // Node journals a run to its session's stream; a child's own stream is
      // modelled as its own session here.
      const sessionId = body.streamId ?? body.sessionId;
      (
        world.store as unknown as {
          sessionOwners: Map<string, { actorId: string; agentKey: null }>;
        }
      ).sessionOwners.set(JSON.stringify([orgId, sessionId]), {
        actorId: ACTOR,
        agentKey: null,
      });
      world.specs.set(body.runId, body.spec);
      if (mongoWorld !== null) {
        await mongoWorld.ensureSession(orgId, ACTOR, sessionId, body.inputText.slice(0, 100));
      }
      await world.store.admit(
        {
          orgId,
          actorId: ACTOR,
          sessionId,
          idempotencyKey: `key-${body.runId}`,
          payloadHash: `sha256:${body.runId}`,
          runId: body.runId,
          dispatchCommandId: `dispatch:${body.runId}`,
          absoluteDeadlineAt: world.now + 24 * 60 * 60 * 1000,
          input: {
            query: body.inputText,
            recordIds: [],
            filters: {},
            attachments: [],
            ...(body.inputArtifacts !== undefined
              ? { inputArtifacts: body.inputArtifacts }
              : {}),
            model: { chatMode: 'auto' },
          },
          harnessMajor: 2,
          via: 'session',
        },
        world.now,
      );
      return { orgId, sessionId };
    }),
  );
  control.post(
    '/schedule',
    handle(async (req) => {
      const body = req.body as {
        cron: string;
        timeZone: string;
        inputText: string;
        spec: RunSpec;
        catchUp?: 'skip' | 'run_latest';
      };
      const service = new ScheduleService({ transaction: testControlPlane(),
        store: world.schedules,
        firer: newFirer(),
        actors: scheduleActors,
        setting: scheduledRunsOn,
      });
      const { schedule } = await service.create(
        { orgId: ORG, userId: ACTOR, via: 'session' },
        {
          name: 'lane schedule',
          input: { query: body.inputText },
          trigger: {
            kind: 'cron',
            cron: body.cron,
            timeZone: body.timeZone,
            ...(body.catchUp !== undefined ? { catchUp: body.catchUp } : {}),
          },
        },
      );
      world.scheduleSpecs.set(schedule.id, body.spec);
      return schedule;
    }),
  );
  control.post(
    '/schedule-tick',
    handle(async (req) => {
      const body = req.body as { scheduleId: string; firers?: number };
      const due = await world.schedules.get(ORG, body.scheduleId);
      if (due === null) throw new Error('no such schedule');
      // The lane's clock follows the wall's: run the schedule's clock to just past its next slot for this tick.
      world.scheduleNow =
        due.nextFireAt === null ? null : Math.max(world.now, due.nextFireAt + 1_000);
      try {
        await Promise.all(
          Array.from({ length: body.firers ?? 2 }, () => newFirer().tick()),
        );
      } finally {
        world.scheduleNow = null;
      }
      return {
        firings: await world.schedules.firings(ORG, body.scheduleId, 50),
        schedule: await world.schedules.get(ORG, body.scheduleId),
      };
    }),
  );
  control.post(
    '/steer',
    handle(async (req) => {
      const body = req.body as {
        sessionId: string;
        text: string;
        attachments?: Array<Record<string, unknown>>;
        recordIds?: string[];
      };
      const steered = await world.store.admit(
        {
          orgId: ORG,
          actorId: ACTOR,
          sessionId: body.sessionId,
          idempotencyKey: `steer-${String(world.now)}-${String(Math.random())}`,
          payloadHash: `sha256:steer-${String(Math.random())}`,
          runId: `unused-${String(Math.random())}`,
          dispatchCommandId: `dispatch:steer-${String(Math.random())}`,
          absoluteDeadlineAt: world.now + 60_000,
          input: {
            query: body.text,
            recordIds: body.recordIds ?? [],
            filters: {},
            attachments: body.attachments ?? [],
            model: { chatMode: 'auto' },
          },
          harnessMajor: 2,
          via: 'session',
          steerOnly: true,
          // The admission service authorized them (not modelled here).
          referencesAuthorized: true,
        },
        world.now,
      );
      return { messageSeq: steered.queued?.messageSeq ?? null };
    }),
  );
  control.post(
    '/advance',
    handle(async (req) => {
      world.now += Math.round(Number(req.body['seconds']) * 1000);
      return { now: world.now };
    }),
  );
  control.get(
    '/now',
    handle(async () => ({ now: world.now })),
  );
  control.post(
    '/sweep',
    handle(async () => ({ swept: await world.service.sweep(100) })),
  );
  control.post(
    '/cancel',
    handle(async (req) => {
      const runId = String(req.body['runId']);
      const run = await world.service.get(ORG, runId);
      if (run === null) throw new Error('unknown run');
      await world.service.control({
        commandId: `cancel:${runId}:${String(run.revision)}`,
        dispatchCommandId: `cancel:${runId}:${String(run.revision)}:dispatch`,
        orgId: ORG,
        runId,
        expectedRevision: run.revision,
        action: 'cancel',
      });
    }),
  );
  control.post(
    '/resolve',
    handle(async (req) => {
      // resolveActive (P2.6c): a running run learns the decision, a suspended one resumes.
      const runId = String(req.body['runId']);
      const interactionId = String(req.body['interactionId']);
      await new InteractionService(world.service).resolve({
        orgId: ORG,
        runId,
        interactionId,
        decision: req.body['decision'],
        ...(req.body['scope'] === 'conversation' ? { scope: 'conversation' as const } : {}),
        channel: 'web',
        // The person every lane run works for: only they may allow a call for the conversation (ADR-GRANT-01 D9).
        by: principalRefFor(ACTOR),
        commandId: `resolve:${runId}:${interactionId}:${String(req.body['decision'])}`,
      });
    }),
  );
  control.post(
    '/shred',
    handle(async () => ({ versions: await world.keys.keys.shred(ORG) })),
  );
  control.post(
    '/shred-workspace-key',
    handle(async (req) => ({
      dropped: await world.workspaceKeys.shred(
        String(req.body['orgId'] ?? ORG),
        String(req.body['sessionId']),
      ),
    })),
  );
  control.post(
    '/tamper-content',
    handle(async () => {
      for (const [ref, bytes] of world.contentBlobs.objects) {
        const flipped = Buffer.from(bytes);
        flipped[flipped.length - 1] = (flipped[flipped.length - 1] ?? 0) ^ 0xff;
        world.contentBlobs.objects.set(ref, flipped);
      }
    }),
  );
  control.get(
    '/content-bytes',
    handle(async () => ({
      objects: [...world.contentBlobs.objects.values()].map((bytes) =>
        bytes.toString('base64'),
      ),
    })),
  );
  // E1's crash matrix reads where a run is and what it journaled, as Node holds it.
  control.get(
    '/run',
    handle(async (req) => {
      const run = await world.service.get(
        queryText(req.query['orgId'] ?? ORG),
        queryText(req.query['runId']),
      );
      return run === null
        ? { status: null }
        : {
            status: run.status,
            attempts: run.attempts,
            attemptId: run.attemptId,
            fence: run.fence,
          };
    }),
  );
  control.get(
    '/events',
    handle(async (req) => {
      const orgId = queryText(req.query['orgId'] ?? ORG);
      const runId = queryText(req.query['runId']);
      const events = await world.store.journal.readRun(orgId, runId, 0, 100_000);
      return { events: await world.journals.resolve(events) };
    }),
  );
  control.get(
    '/gateway-public-keys',
    handle(async () => (await gatewayPublicKeys.read()).value),
  );
  control.get(
    '/requests',
    handle(async () => ({ requests })),
  );
  const budgetCap = (raw: unknown): BudgetCap => {
    const cap = (raw ?? {}) as { usd?: number | null; tokens?: number | null };
    return { usdMicros: cap.usd ?? null, tokens: cap.tokens ?? null };
  };
  const leaseBudgetRun = (runId: string, parentRunId: string | null): void => {
    if (!world.budgetRuns.has(runId)) {
      world.budgetRuns.set(runId, { parentRunId, pin: null });
    }
  };
  control.post(
    '/budget/run',
    handle(async (req) => {
      const body = req.body as {
        runId: string;
        parentRunId?: string | null;
        maxCostUsd?: number;
        maxTokens?: number;
        attempt?: { attemptId: string; fence: number };
      };
      world.budgetRuns.set(body.runId, {
        parentRunId: body.parentRunId ?? null,
        pin:
          body.maxCostUsd === undefined || body.maxTokens === undefined
            ? null
            : { maxCostUsd: body.maxCostUsd, maxTokens: body.maxTokens },
        ...(body.attempt !== undefined ? { attempt: body.attempt } : {}),
      });
    }),
  );
  control.post(
    '/budget/open-root',
    handle(async (req) => {
      const runId = String(req.body['runId']);
      leaseBudgetRun(runId, null);
      const opening = req.body['opening'] as BudgetAmount | undefined;
      return world.budgets.openRoot(ORG, runId, budgetCap(req.body['cap']), opening);
    }),
  );
  control.post(
    '/budget/open-child',
    handle(async (req) => {
      const runId = String(req.body['runId']);
      const parentRunId = String(req.body['parentRunId']);
      leaseBudgetRun(runId, parentRunId);
      return world.budgets.openChild(ORG, parentRunId, runId, budgetCap(req.body['cap']));
    }),
  );
  // A settled call with its usage, as the model gateway commits one (C9.01's reconciliation reads it back).
  control.post(
    '/budget/charge',
    handle(async (req) => {
      const runId = String(req.body['runId']);
      const key = String(req.body['key']);
      const usage = req.body['usage'] as BudgetUsage;
      await world.budgets.reserve({
        orgId: ORG,
        runId,
        key,
        requested: { usdMicros: 0, tokens: 0 },
        minimum: { usdMicros: 0, tokens: 0 },
        ttlMs: 60_000,
      });
      const tokens = usage.inputTokens + usage.outputTokens;
      return world.budgets.commit(ORG, runId, key, { usdMicros: 0, tokens }, usage);
    }),
  );
  control.post(
    '/budget/close',
    handle(async (req) => {
      await world.budgets.close(ORG, String(req.body['runId']));
    }),
  );
  control.post(
    '/budget/token',
    handle(async (req) => {
      const runId = String(req.body['runId']);
      leaseBudgetRun(runId, null);
      const issued = await budgetGatewayTokens.issue({
        binding: 'run',
        orgId: ORG,
        sessionId: `budget-${runId}`,
        runId,
        parentRunId: world.budgetRuns.get(runId)?.parentRunId ?? null,
        attemptId: BUDGET_ATTEMPT,
        fence: 1,
        toolIdsDigest: 'tools',
        routesDigest: 'routes',
      });
      return { token: issued.token };
    }),
  );
  // Where a run's dispatch hints are in Node's outbox (P9 FM-1: held while the broker is down).
  control.get(
    '/outbox',
    handle(async (req) => {
      if (mongoWorld === null) throw new Error('Mongo mode only');
      const rows = await mongoose.connection
        .db!.collection('outbox_events')
        .find({ orderingKey: queryText(req.query['runId']) })
        .project({ status: 1, attempts: 1 })
        .toArray();
      return { rows: rows.map((row) => ({ status: row['status'], attempts: row['attempts'] ?? 0 })) };
    }),
  );
  // What the backup drill checks of a session (P9 BR-3): its events, how many are erased tombstones, its key.
  control.get(
    '/session-state',
    handle(async (req) => {
      if (mongoWorld === null) throw new Error('Mongo mode only');
      const orgId = queryText(req.query['orgId'] ?? ORG);
      const sessionId = queryText(req.query['sessionId']);
      const db = mongoose.connection.db!;
      const key = await db.collection('agentWorkspaceKeys').findOne({ orgId, sessionId });
      return {
        events: await db.collection('sessionEvents').countDocuments({ orgId, sessionId }),
        erased: await db.collection('sessionEvents').countDocuments({ orgId, sessionId, erased: true }),
        sessionErased: (await db.collection('sessionEvents').countDocuments({ orgId, sessionId, type: 'session.erased' })) > 0,
        key: key === null ? 'absent' : key['wrapped'] === null ? 'destroyed' : 'present',
        manifests: await db.collection('agentWorkspaceManifests').countDocuments({ orgId, workspaceId: { $regex: `^ws:${sessionId}` } }),
      };
    }),
  );
  // The lane's own database, dropped at its teardown (never another).
  control.post(
    '/drop-database',
    handle(async () => {
      if (mongoWorld === null) throw new Error('Mongo mode only');
      await mongoose.connection.dropDatabase();
    }),
  );
  control.post(
    '/erase',
    handle(async (req) => {
      if (mongoWorld === null) throw new Error('erasure runs in Mongo mode only');
      return {
        results: await mongoWorld.erase(
          String(req.body['orgId'] ?? ORG),
          String(req.body['sessionId']),
          world.service,
          () => world.service.sweep(100),
        ),
      };
    }),
  );
  // P7.3: the org's memory policy and memories as admins and users left them.
  control.post(
    '/memory/policy',
    handle(async (req) => {
      const policy = req.body as MemoryPolicy;
      world.memoryPolicy = policy;
      return policy;
    }),
  );
  control.post(
    '/memory/seed',
    handle(async (req) => {
      const entry = { orgId: ORG, ownerId: ACTOR, ...(req.body as Partial<MemoryEntry>) } as MemoryEntry;
      return world.memoryStore.propose(entry);
    }),
  );
  control.post(
    '/memory/delete',
    handle(async (req) => ({
      deleted: await world.memoryStore.delete(ORG, String(req.body['memoryId'])),
    })),
  );
  control.get(
    '/memory/get',
    handle(async (req) => ({
      memory: await world.memoryStore.get(ORG, queryText(req.query['memoryId'])),
    })),
  );
  control.get(
    '/memory/list',
    handle(async (req) => ({
      memories: await world.memoryStore.list(ORG, { runId: queryText(req.query['runId']), limit: 50 }),
    })),
  );
  app.use('/__control', control);
  app.use('/__control', extensionControl);
  app.use(ErrorMiddleware.handleError());

  const host = process.env['JOURNAL_HTTP_HOST'] ?? '127.0.0.1';
  if (MONGO_MODE && process.env['JOURNAL_BROKER_REDIS']) {
    await startOutboxDispatcher(process.env['JOURNAL_BROKER_REDIS']);
  }
  const server = app.listen(Number(process.env['JOURNAL_HTTP_PORT'] ?? 0), host, () => {
    const { port } = server.address() as AddressInfo;
    process.stdout.write(`READY ${String(port)} ${String(credential)} ${String(proxyCredential)}\n`);
  });
  const stop = (): void => {
    server.close(() => process.exit(0));
  };
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
  // The Python suite owns this process; it goes when its stdin closes.
  process.stdin.on('end', stop);
  process.stdin.resume();
}

/** Node's outbox relay, publishing dispatch hints to Redis Streams at `host:port` (P9 FM-1). */
async function startOutboxDispatcher(address: string): Promise<void> {
  const [host, port] = address.split(':');
  const logger = Logger.getInstance({ service: 'journal-server' });
  const broker: ResolvedMessageBrokerConfig = {
    type: MessageBrokerType.REDIS,
    redis: buildRedisBrokerConfig({ host: host as string, port: Number(port), db: 0 }),
  };
  // A broker that is down at start must not keep this server from serving: the dispatcher retries.
  void ensureMessageTopicsExist(broker, logger).catch(() => undefined);
  const producer = createMessageProducer(broker, logger);
  void producer.connect().catch(() => undefined);
  new OutboxDispatcher(producer, logger, 200).start();
}

void main();
