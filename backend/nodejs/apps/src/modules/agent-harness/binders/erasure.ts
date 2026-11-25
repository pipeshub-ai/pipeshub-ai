import { getSlackUserLinkRepository } from '../../../libs/repositories/slack-user-link';
import { createInboxStore } from '../../../libs/repositories/agent-harness/inbox';
import { createSandboxSnapshotIndex } from '../../../libs/repositories/agent-harness/sandbox-snapshots';
import type { CodingEnginePreferences } from '../ports/coding-engines';
import type { OrgDataKeys } from '../application/org-data-keys';
import { Container } from 'inversify';
import { editionOrgErasers, harnessEditionOf } from '../edition';
import {
  CONTROL_PLANE_BINDINGS,
  CONTROL_PLANE_GRANT_ADMIN,
  CONTROL_PLANE_INSTALLATION_ADMIN,
  ControlPlaneUserEraser,
  type BindingService,
  type GrantAdmin,
  type InstallationAdmin,
} from '../../control-plane';
import type { StoreSession } from '../../../libs/repositories/agent-harness/durable-transaction';
import { hostname } from 'node:os';
import { randomUUID } from 'node:crypto';
import { inDurableTransaction } from '../../../libs/repositories/agent-harness/durable-transaction';
import { createAbandonedForks } from '../../../libs/repositories/agent-harness/abandoned-forks';
import { createAgentErasureQueue } from '../../../libs/repositories/agent-harness/agent-erasures';
import { createLeaderLease } from '../../../libs/repositories/agent-harness/leader-lease';
import { createMemoryStore } from '../../../libs/repositories/agent-harness/memory';
import {
  DATA_ERASER_EXTENSION,
  type DataEraserExtension,
} from '../ports/data-eraser-extension';
import { createScheduleStore } from '../../../libs/repositories/agent-harness/schedules';
import { createRetentionStore } from '../../../libs/repositories/agent-harness/retention-store';
import {
  createErasureJobStore,
  createSessionRunLookup,
} from '../../../libs/repositories/agent-harness/session-erasure';
import type { ErasureJobStore } from '../ports/session-erasure';
import { createSessionStoreEraser } from '../../../libs/repositories/agent-harness/session-store-eraser';
import { ExecutionService } from '../application/execution-service';
import { RetentionSweeper } from '../application/retention-sweeper';
import {
  SessionErasureRunner,
  SessionErasureWorker,
} from '../application/session-erasure';
import { createErasureSteps } from '../application/session-erasure-steps';
import {
  AGENT_HARNESS_SESSION_RUN_STOPPER,
  SessionRunStopper,
} from '../application/session-run-stopper';
import type { ExecutionStore } from '../ports/execution-store';
import { AgentDataEraser, AgentErasureQueue } from '../ports/agent-erasures';
import { MemoryEraser, MemoryStore } from '../ports/memory-store';
import { PersonalInstanceEraser } from '../ports/personal-instances';
import { SandboxFolderEraser, SandboxReaper } from '../ports/sandbox-reaper';
import {
  RetentionPass,
  NO_RETENTION,
  SessionRetentionSettings,
} from '../ports/session-retention';
import { boundOrNull, HarnessBinder } from './context';
import { costEraserOf } from './cost-ports';
import { RELEASE_SNAPSHOT_TTL } from '../application/release-run-settings';
import type { SnapshotTtlSource } from '../ports/sandbox-snapshots';
import { workspaceKeys } from './workspaces';
import {
  AGENT_HARNESS_AGENT_ERASURES,
  AGENT_HARNESS_ERASURE_WORKER,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_MEMORY_STORE,
  AGENT_HARNESS_RETENTION_SWEEPER,
  AGENT_HARNESS_SANDBOX_REAPER,
  AGENT_HARNESS_SESSION_ERASURES,
  AGENT_HARNESS_RETENTION_PASS,
  AGENT_HARNESS_SESSION_RETENTION,
  AGENT_HARNESS_SNAPSHOT_TTL,
  AGENT_HARNESS_SESSION_PARTICIPANTS,
} from './tokens';
import {
  createOrgData,
  createPrincipalErasureJobs,
} from '../../../libs/repositories/agent-harness/principal-erasure';
import {
  OrgErasureParts,
  OrgErasureSteps,
  OrgPrincipalEraser,
  PrincipalErasureRunner,
} from '../application/principal-erasure';
import { ImmediateOrgErasureSteps } from '../application/org-erasure-steps';
import { silentHarnessLogger } from '../application/harness-log';
import { engineSignInEraser } from '../application/engine-signin-service';
import { createEngineSignIns } from '../../../libs/repositories/agent-harness/engine-signin';
import type { EngineSignInRelay } from '../ports/engine-signins';
import type { ModelAccountEraser } from '../ports/model-accounts';
import {
  SandboxOrgEraser,
  SessionOwnerRecorder,
} from '../ports/principal-erasure';
import type { AgentHarnessDependencies } from '../composition';
import { Logger } from '../../../libs/services/logger.service';

/**
 * What agent deletion erases through (H2, C-14 AGENT_DATA_STORES): the
 * agent's own memories (the harness's store when one is composed, else
 * Mongo's, as user and org deletion do), then its schedules (disabled with
 * their templates dropped). Each is idempotent, so a retry repeats both.
 */
export function agentDataEraser(container: Container): AgentDataEraser {
  const memories: Pick<MemoryEraser, 'eraseAgent'> = container.isBound(
    AGENT_HARNESS_MEMORY_STORE,
  )
    ? container.get<MemoryStore & MemoryEraser>(AGENT_HARNESS_MEMORY_STORE)
    : createMemoryStore();
  const stores: readonly AgentDataEraser[] = [memories, createScheduleStore()];
  return {
    async eraseAgent(orgId, agentKey) {
      // In order; one failing fails the whole, so the retry runs them all again.
      let erased = 0;
      for (const store of stores)
        erased += await store.eraseAgent(orgId, agentKey);
      return erased;
    },
  };
}

export const bindSessionErasure: HarnessBinder = ({
  container,
  dependencies,
  admissionStore,
  journal,
  logger,
  runKeys,
}) => {
  if (!container.isBound(AGENT_HARNESS_SESSION_ERASURES)) {
    container
      .bind<ErasureJobStore<StoreSession>>(AGENT_HARNESS_SESSION_ERASURES)
      .toConstantValue(createErasureJobStore());
  }
  if (!container.isBound(AGENT_HARNESS_SESSION_RUN_STOPPER)) {
    container
      .bind<SessionRunStopper>(AGENT_HARNESS_SESSION_RUN_STOPPER)
      .toDynamicValue(
        (context) =>
          new SessionRunStopper({
            runs: context.container.get<ExecutionStore>(
              AGENT_HARNESS_EXECUTION_STORE,
            ),
            executions: context.container.get<ExecutionService>(
              AGENT_HARNESS_EXECUTION_SERVICE,
            ),
          }),
      )
      .inSingletonScope();
  }
  const erasure = dependencies.erasure;
  if (
    erasure === undefined ||
    container.isBound(AGENT_HARNESS_ERASURE_WORKER)
  ) {
    return;
  }
  container
    .bind<SessionErasureWorker<StoreSession>>(AGENT_HARNESS_ERASURE_WORKER)
    .toDynamicValue((context) => {
      const jobs = context.container.get<ErasureJobStore<StoreSession>>(
        AGENT_HARNESS_SESSION_ERASURES,
      );
      const runner = new SessionErasureRunner({
        jobs,
        erasers: createErasureSteps<StoreSession>({
          runs: createSessionRunLookup(),
          nextRuns: admissionStore,
          executions: context.container.get<ExecutionService>(
            AGENT_HARNESS_EXECUTION_SERVICE,
          ),
          workspaceKeys: workspaceKeys({ journal, logger }),
          liveBus: erasure.liveBus,
          memories: boundOrNull<MemoryStore>(
            context.container,
            AGENT_HARNESS_MEMORY_STORE,
          ),
          extension: boundOrNull<DataEraserExtension>(
            context.container,
            DATA_ERASER_EXTENSION,
          ),
          sandboxFolders: boundOrNull<SandboxReaper & SandboxFolderEraser>(
            context.container,
            AGENT_HARNESS_SANDBOX_REAPER,
          ),
          artifacts: erasure.artifacts,
          traces: erasure.traces ?? null,
          stores: createSessionStoreEraser(journal.cipher, runKeys),
          conversations: erasure.conversations,
          engineChoices: dependencies.codingEnginePreferences ?? null,
          journal: {
            store: journal.store,
            preparer: journal.service,
            transaction: inDurableTransaction,
          },
        }),
        conversations: erasure.conversations,
        logger,
      });
      return new SessionErasureWorker({
        jobs,
        runner,
        lease: createLeaderLease(),
        holderId: `${hostname()}:${String(process.pid)}:${randomUUID()}`,
        logger,
      });
    })
    .inSingletonScope();
};

/**
 * The principal stores a deleted org's erasure empties (C-14 PRINCIPAL_DATA_STORES): model accounts, schedules, memories, the
 * engine sign-ins (signed out through the relay first), and its members'
 * personal toolset and MCP instances.
 */
export function orgPrincipalErasers(deps: {
  modelAccounts: ModelAccountEraser;
  personalInstances: PersonalInstanceEraser;
  signInRelay?: Pick<EngineSignInRelay, 'revoke'>;
}): OrgPrincipalEraser[] {
  const signIns = engineSignInEraser(createEngineSignIns(), deps.signInRelay);
  return [
    {
      store: 'modelAccounts',
      eraseOrg: (orgId) => deps.modelAccounts.eraseOrg(orgId),
    },
    {
      // ADR-SLACKID-01: the org's Slack account links (an email and a Slack name each).
      store: 'slackUserLinks',
      eraseOrg: (orgId) => getSlackUserLinkRepository().removeForOrg(orgId),
    },
    {
      store: 'agentSchedules',
      eraseOrg: (orgId) => createScheduleStore().eraseOrg(orgId),
    },
    {
      store: 'agentMemories',
      eraseOrg: (orgId) => createMemoryStore().eraseOrg(orgId),
    },
    { store: 'engineSignIns', eraseOrg: (orgId) => signIns.eraseOrg(orgId) },
    { store: 'inbox', eraseOrg: (orgId) => createInboxStore().eraseOrg(orgId) },
    {
      store: 'personalInstances',
      eraseOrg: (orgId) => deps.personalInstances.eraseOrg(orgId),
    },
  ];
}

/** The bound `DataEraserExtension`'s org erasure, as one more principal eraser; none unbound. */
function extensionOrgErasers(container: Container): OrgPrincipalEraser[] {
  const extension = boundOrNull<DataEraserExtension>(
    container,
    DATA_ERASER_EXTENSION,
  );
  return extension === null
    ? []
    : [
        {
          store: 'extensionStores',
          eraseOrg: (orgId) => extension.eraseOrg(orgId),
        },
      ];
}

/** Org and user deletions after their request (CLEANUP-G1, G2), run by the retention sweeper. */
function principalErasureRunner(
  container: Container,
  erasure: NonNullable<AgentHarnessDependencies['erasure']>,
  keys: Pick<OrgDataKeys, 'shred'>,
  logger: Logger | undefined,
  engineChoices: Pick<CodingEnginePreferences, 'eraseUser'> | null,
): PrincipalErasureRunner<StoreSession> {
  const recorder = boundOrNull<Partial<SessionOwnerRecorder<StoreSession>>>(
    container,
    AGENT_HARNESS_SESSION_PARTICIPANTS,
  );
  return new PrincipalErasureRunner<StoreSession>({
    jobs: createPrincipalErasureJobs(),
    orgSteps: orgErasureSteps(container, erasure, keys, logger),
    recorder:
      recorder !== null && typeof recorder.ownerChanged === 'function'
        ? (recorder as SessionOwnerRecorder<StoreSession>)
        : null,
    unsent:
      erasure.unsentAttachments !== undefined
        ? {
            uploads: erasure.unsentAttachments.uploads,
            eraser: erasure.unsentAttachments.eraser,
          }
        : null,
    controlPlane: controlPlaneUserEraser(container),
    costs: costEraserOf(container),
    engineChoices,
    transaction: inDurableTransaction,
    ...(logger !== undefined ? { logger } : {}),
  });
}

/** The org erasure steps, built from the parts any org erasure uses: the edition's, or the built-in ones. */
function orgErasureSteps(
  container: Container,
  erasure: NonNullable<AgentHarnessDependencies['erasure']>,
  keys: Pick<OrgDataKeys, 'shred'>,
  logger: Logger | undefined,
): OrgErasureSteps {
  const make = harnessEditionOf(container).orgErasure;
  const reaper = boundOrNull<
    SandboxReaper & SandboxFolderEraser & Partial<SandboxOrgEraser>
  >(container, AGENT_HARNESS_SANDBOX_REAPER);
  const parts: OrgErasureParts<StoreSession> = {
    jobs: createPrincipalErasureJobs(),
    // Through the key ring, so this replica's cache drops the keys with them; others' expire within DATA_KEY_CACHE_TTL_MS.
    org: createOrgData(keys),
    sessions: container.get<ErasureJobStore<StoreSession>>(
      AGENT_HARNESS_SESSION_ERASURES,
    ),
    executions: container.get<ExecutionService>(
      AGENT_HARNESS_EXECUTION_SERVICE,
    ),
    reaper,
    sandboxes:
      reaper !== null && typeof reaper.eraseOrg === 'function'
        ? (reaper as SandboxOrgEraser)
        : null,
    queryService: erasure.orgQueryService ?? null,
    principals: [
      ...(erasure.orgPrincipals ?? []),
      ...editionOrgErasers(container),
      ...extensionOrgErasers(container),
    ],
    redis: erasure.orgRedis ?? null,
    announcer: erasure.orgDeletedAnnouncer ?? null,
    transaction: inDurableTransaction,
    logger: logger ?? silentHarnessLogger,
  };
  return make === null
    ? new ImmediateOrgErasureSteps(parts)
    : make(container, parts);
}

/** The control plane's side of a user deletion (CLEANUP-G2c), where its admin services are composed. */
function controlPlaneUserEraser(
  container: Container,
): ControlPlaneUserEraser | null {
  const installations = boundOrNull<InstallationAdmin>(
    container,
    CONTROL_PLANE_INSTALLATION_ADMIN,
  );
  const grants = boundOrNull<GrantAdmin>(container, CONTROL_PLANE_GRANT_ADMIN);
  if (installations === null || grants === null) return null;
  return new ControlPlaneUserEraser({
    installations,
    grants,
    bindings: boundOrNull<BindingService>(container, CONTROL_PLANE_BINDINGS),
  });
}

export const bindRetention: HarnessBinder = ({
  container,
  dependencies,
  journal,
  logger,
}) => {
  const erasure = dependencies.erasure;
  if (
    erasure === undefined ||
    container.isBound(AGENT_HARNESS_RETENTION_SWEEPER)
  ) {
    return;
  }
  container
    .bind<AgentErasureQueue>(AGENT_HARNESS_AGENT_ERASURES)
    .toConstantValue(erasure.agentErasures ?? createAgentErasureQueue());
  container
    .bind<RetentionSweeper<StoreSession>>(AGENT_HARNESS_RETENTION_SWEEPER)
    .toDynamicValue(
      (context) =>
        new RetentionSweeper<StoreSession>({
          store: createRetentionStore(),
          settings:
            boundOrNull<SessionRetentionSettings>(
              context.container,
              AGENT_HARNESS_SESSION_RETENTION,
            ) ?? NO_RETENTION,
          ...(context.container.isBound(AGENT_HARNESS_RETENTION_PASS)
            ? {
                extraPass: context.container.get<RetentionPass>(
                  AGENT_HARNESS_RETENTION_PASS,
                ),
              }
            : {}),
          conversations: erasure.retention,
          erasures: context.container.get<ErasureJobStore<StoreSession>>(
            AGENT_HARNESS_SESSION_ERASURES,
          ),
          runs: createSessionRunLookup(),
          artifacts: erasure.artifacts,
          agentErasures: {
            queue: context.container.get<AgentErasureQueue>(
              AGENT_HARNESS_AGENT_ERASURES,
            ),
            memories: agentDataEraser(container),
            deletions: erasure.agentDeletions,
          },
          abandonedForks: createAbandonedForks(),
          unsentAttachments: erasure.unsentAttachments ?? null,
          snapshots:
            erasure.snapshotObjects !== undefined
              ? {
                  index: createSandboxSnapshotIndex(),
                  ttl:
                    boundOrNull<SnapshotTtlSource>(
                      context.container,
                      AGENT_HARNESS_SNAPSHOT_TTL,
                    ) ?? RELEASE_SNAPSHOT_TTL,
                  objects: erasure.snapshotObjects,
                }
              : null,
          costLedger: costEraserOf(context.container),
          principalErasures: principalErasureRunner(
            container,
            erasure,
            journal.keys,
            logger,
            dependencies.codingEnginePreferences ?? null,
          ),
          transaction: inDurableTransaction,
          lease: createLeaderLease(),
          holderId: `${hostname()}:${String(process.pid)}:${randomUUID()}`,
          logger,
        }),
    )
    .inSingletonScope();
};
