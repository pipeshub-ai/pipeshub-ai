import type { OperatorStops } from '../ports/operator-stops';
import { AGENT_HARNESS_OPERATOR_CONTROLS } from './tokens';
import { groupAccessOf } from './group-access';
import { claimFloorEvery } from '../config';
import { HttpEngineAvailability } from '../adapters/http/http-engine-availability';
import { createExecutionStore } from '../../../libs/repositories/agent-harness/execution';
import type { ApproverNoticeOutbox } from '../ports/approval-links';
import type { Tx } from '../../../libs/repositories/uow';
import { createMemoryStore } from '../../../libs/repositories/agent-harness/memory';
import { createWorkerFeatureSightings } from '../../../libs/repositories/agent-harness/worker-feature-sightings';
import { prometheusChatPathMetrics } from '../adapters/telemetry/chat-path-metrics';
import { createRunState } from '../../../libs/repositories/agent-harness/run-state';
import {
  HarnessLocalMetrics,
  LocalMetrics,
} from '../application/harness-local-metrics';
import { metricsBackend } from '../../../libs/services/telemetry/metrics-backend';
import { prometheusWorkerFeatureMetrics } from '../adapters/telemetry/worker-feature-metrics';
import { HarnessCapabilityService } from '../application/capability-service';
import {
  childEngineAuthority,
  ChildRunService,
} from '../application/child-run-service';
import { engineCarry } from '../application/engine-carry';
import { EngineEligibility } from '../application/engine-eligibility';
import type { EngineImageSource } from '../application/engine-image-pin';
import { HarnessAuthority } from '../application/harness-authority';
import { ExecutionService } from '../application/execution-service';
import {
  AGENT_HARNESS_MEMORY_SERVICE_FACTORY,
  MemoryService,
  type MemoryServiceFactory,
} from '../application/memory-service';
import { ModelAccessService } from '../application/model-access-service';
import { NextRunService } from '../application/next-run-service';
import { SharedCitationService } from '../application/shared-citations';
import { WorkerFeatureAvailability } from '../application/worker-feature-availability';
import { WorkspaceService } from '../application/workspace-service';
import { profileExecutor, runSpecResolver } from '../domain/run-spec';
import { EngineProfileCeilings } from '../ports/engine-profiles';
import type { ExecutionStore } from '../ports/execution-store';
import {
  AGENT_HARNESS_MEMORY_SETTINGS,
  MemoryEraser,
  MemorySettings,
  MemoryStore,
} from '../ports/memory-store';
import { PERSONAL_MEMORY } from '../domain/memory';
import { Logger } from '../../../libs/services/logger.service';
import { boundOrNull, HarnessBinder, HarnessBindingContext } from './context';
import { harnessLogMeta } from '../application/harness-log';
import { AGENT_HARNESS_CHILD_PINNING } from './control-plane-pin';
import type { ChildPinning } from '../application/admission/child-pinning';
import { guardedWorkerFeatures } from '../domain/worker-compat';
import { recordRunCacheRead } from '../application/run-cache-read';
import type { SessionRoleLookup } from '../ports/session-roles';
import { policyStoreIsRecord } from './policy-store';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_APPROVER_NOTICE_OUTBOX,
  AGENT_HARNESS_CAPABILITIES,
  AGENT_HARNESS_CHILD_RUNS,
  AGENT_HARNESS_ENGINE_PROFILES,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_MEMORIES,
  AGENT_HARNESS_MEMORY_STORE,
  AGENT_HARNESS_MODEL_ACCESS,
  AGENT_HARNESS_NEXT_RUNS,
  AGENT_HARNESS_SHARED_CITATIONS,
  AGENT_HARNESS_WORKER_FEATURES,
  AGENT_HARNESS_WORKSPACES,
  AGENT_HARNESS_LOCAL_METRICS,
} from './tokens';

/**
 * C2.20: the sandbox broker's engine image digests a claim or a child admission pins, asked afresh each time
 * (no cache), so a run admitted right after an image is rebuilt pins the new image, never the one it replaced.
 */
const brokerImages = (
  authority: () => HarnessAuthority,
  dependencies: HarnessBindingContext['dependencies'],
): EngineImageSource | null =>
  dependencies.sandboxBrokerEndpoint === undefined
    ? null
    : new HttpEngineAvailability(
        dependencies.sandboxBrokerEndpoint,
        () => authority().workerCredential(),
        fetch,
        { ttlMs: 0 },
      );

export const bindExecutionStore: HarnessBinder = ({
  container,
  dependencies,
  dispatches,
  journal,
}) => {
  if (container.isBound(AGENT_HARNESS_EXECUTION_STORE)) return;
  if (!container.isBound(AGENT_HARNESS_LOCAL_METRICS)) {
    // C9.04: the merged metrics route's own part: Node's harness families and the run store read at scrape time.
    container.bind<LocalMetrics>(AGENT_HARNESS_LOCAL_METRICS).toConstantValue(
      new HarnessLocalMetrics({
        registry: () => metricsBackend.serialize(),
        runs: createRunState(),
        // Resolved at each scrape: the operator controls are bound by a later binder.
        controls: () =>
          boundOrNull<OperatorStops>(
            container,
            AGENT_HARNESS_OPERATOR_CONTROLS,
          )?.controls() ?? Promise.resolve(null),
      }),
    );
  }
  // WP56-OUTBOX: an edition's outbox, written in the commit that makes a run wait; none bound, nothing is written.
  const approverNotices = boundOrNull<ApproverNoticeOutbox<Tx>>(
    container,
    AGENT_HARNESS_APPROVER_NOTICE_OUTBOX,
  );
  container.bind<ExecutionStore>(AGENT_HARNESS_EXECUTION_STORE).toConstantValue(
    createExecutionStore({
      conversations: dependencies.conversations,
      dispatches,
      journal: journal.store,
      approverNotices,
    }),
  );
};

/**
 * WP-111: the one engine-eligibility policy over what the container binds (the org ceilings, the access groups, the
 * operator controls), resolved at each check, so a binder's order never leaves a switch unread.
 */
const engineEligibility = (
  container: Parameters<typeof boundOrNull>[0],
): Pick<EngineEligibility, 'check'> => ({
  check: (orgId, actorId, engine, known) =>
    new EngineEligibility({
      ceilings: boundOrNull<EngineProfileCeilings>(
        container,
        AGENT_HARNESS_ENGINE_PROFILES,
      ),
      groupAccess: groupAccessOf(container) ?? null,
      operator: boundOrNull<OperatorStops>(
        container,
        AGENT_HARNESS_OPERATOR_CONTROLS,
      ),
    }).check(orgId, actorId, engine, known),
});

export const bindChildRuns: HarnessBinder = ({
  container,
  childRuns,
  dependencies,
  journal,
  logger,
}) => {
  if (container.isBound(AGENT_HARNESS_CHILD_RUNS)) return;
  container
    .bind<ChildRunService>(AGENT_HARNESS_CHILD_RUNS)
    .toDynamicValue(
      (context) =>
        new ChildRunService({
          runs: context.container.get<ExecutionStore>(
            AGENT_HARNESS_EXECUTION_STORE,
          ),
          children: childRuns,
          journals: journal.service,
          logger,
          workspaces: boundOrNull<WorkspaceService>(
            context.container,
            AGENT_HARNESS_WORKSPACES,
          ),
          engineAuthority: childEngineAuthority(
            boundOrNull<EngineProfileCeilings>(
              context.container,
              AGENT_HARNESS_ENGINE_PROFILES,
            ),
          ),
          pinning: boundOrNull<ChildPinning>(
            context.container,
            AGENT_HARNESS_CHILD_PINNING,
          ),
          configuredLlms: dependencies.pin?.models ?? null,
          groupAccess: groupAccessOf(context.container) ?? null,
          operatorControls: boundOrNull<OperatorStops>(
            context.container,
            AGENT_HARNESS_OPERATOR_CONTROLS,
          ),
          engineEligibility: engineEligibility(context.container),
          workerFeatures: boundOrNull<WorkerFeatureAvailability>(
            context.container,
            AGENT_HARNESS_WORKER_FEATURES,
          ),
          engineCarry: engineCarry({
            runs: context.container.get<ExecutionStore>(
              AGENT_HARNESS_EXECUTION_STORE,
            ),
            journal: journal.runJournal,
          }),
          sessionShared: sessionSharedLookup(dependencies.roles),
          engineImages: brokerImages(
            () =>
              context.container.get<HarnessAuthority>(AGENT_HARNESS_AUTHORITY),
            dependencies,
          ),
          engineAccess: context.container.isBound(AGENT_HARNESS_MODEL_ACCESS)
            ? (subject) =>
                context.container
                  .get<ModelAccessService>(AGENT_HARNESS_MODEL_ACCESS)
                  .decide(subject)
            : null,
        }),
    )
    .inSingletonScope();
};

export const bindExecution: HarnessBinder = (context) => {
  const { container, journal, childRuns, dependencies, logger } = context;
  if (container.isBound(AGENT_HARNESS_EXECUTION_SERVICE)) return;
  const executionStore = container.get<ExecutionStore>(
    AGENT_HARNESS_EXECUTION_STORE,
  );
  const executions = new ExecutionService({
    store: executionStore,
    journal: journal.store,
    journals: journal.service,
    history: journal.history,
    inputs: journal.runInput,
    runJournal: journal.runJournal,
    capabilities: container.get<HarnessCapabilityService>(
      AGENT_HARNESS_CAPABILITIES,
    ),
    resolveSpec: runSpecResolver(profileExecutor),
    logger: container.isBound('Logger')
      ? container.get<Logger>('Logger')
      : undefined,
    children: childRuns,
    engineImages: brokerImages(
      () => container.get<HarnessAuthority>(AGENT_HARNESS_AUTHORITY),
      dependencies,
    ),
    budgets: context.budgets,
    claimFloorEvery: claimFloorEvery(),
    engineEligibility: engineEligibility(container),
  });
  if (!container.isBound(AGENT_HARNESS_WORKER_FEATURES)) {
    container
      .bind<WorkerFeatureAvailability>(AGENT_HARNESS_WORKER_FEATURES)
      .toConstantValue(
        new WorkerFeatureAvailability({
          sightings: createWorkerFeatureSightings(),
          logger,
          metrics: prometheusWorkerFeatureMetrics,
        }),
      );
  }
  executions.measureWith(prometheusChatPathMetrics);
  if (dependencies.wakeup != null) {
    executions.wakeOnCancel(dependencies.wakeup);
  }
  executions.watchWorkerFeatures(
    container.get<WorkerFeatureAvailability>(AGENT_HARNESS_WORKER_FEATURES),
    guardedWorkerFeatures(policyStoreIsRecord(container)),
  );
  const nextRuns = container.get<NextRunService>(AGENT_HARNESS_NEXT_RUNS);
  // A run's end admits the message that waited for it (SS2 next_run), and its budget holds go now: nothing else
  // will ask its tree again (CLEANUP-S4).
  executions.onRunEnded((run) => {
    nextRuns.runEnded(run);
    void context.budgets
      .close(run.orgId, run.runId)
      .then((closed) => {
        recordRunCacheRead(prometheusChatPathMetrics, closed);
      })
      .catch((error: unknown) => {
        logger?.warn(
          'budget.close_failed',
          harnessLogMeta('budget.close_failed', {
            orgId: run.orgId,
            runId: run.runId,
            errorType: error instanceof Error ? error.name : typeof error,
          }),
        );
      });
  });
  container
    .bind<ExecutionService>(AGENT_HARNESS_EXECUTION_SERVICE)
    .toConstantValue(executions);
  bindMemories(context, executions, executionStore);
};

/** Memories settle on their run's end, so they bind only with the execution service they listen to. */
function bindMemories(
  { container, dependencies, logger }: HarnessBindingContext,
  executions: ExecutionService,
  executionStore: ExecutionStore,
): void {
  if (!container.isBound(AGENT_HARNESS_MEMORY_STORE)) {
    container
      .bind<MemoryStore & MemoryEraser>(AGENT_HARNESS_MEMORY_STORE)
      .toConstantValue(dependencies.memory?.store ?? createMemoryStore());
  }
  if (
    dependencies.memory === undefined ||
    container.isBound(AGENT_HARNESS_MEMORIES)
  ) {
    return;
  }
  const make =
    boundOrNull<MemoryServiceFactory>(
      container,
      AGENT_HARNESS_MEMORY_SERVICE_FACTORY,
    ) ?? ((options) => new MemoryService(options));
  const memories = make({
    store: container.get<MemoryStore>(AGENT_HARNESS_MEMORY_STORE),
    settings: boundOrNull<MemorySettings>(
      container,
      AGENT_HARNESS_MEMORY_SETTINGS,
    ) ?? { read: () => Promise.resolve(PERSONAL_MEMORY) },
    access: container.get<SharedCitationService>(
      AGENT_HARNESS_SHARED_CITATIONS,
    ),
    runs: executionStore,
    ...(dependencies.memory.choices !== undefined
      ? { choices: dependencies.memory.choices }
      : {}),
    logger,
  });
  // A run's end settles the memories it proposed with its turn label (P7.3).
  executions.onRunLabelled((run, label) => {
    memories.settleQuietly(run, label);
  });
  // A run Node ends without a transcript has no label: its proposals settle as unknown (H2).
  executions.onRunUnlabelled((run) => {
    memories.settleQuietly(run, null);
  });
  container
    .bind<MemoryService>(AGENT_HARNESS_MEMORIES)
    .toConstantValue(memories);
}

function sessionSharedLookup(
  roles: SessionRoleLookup | undefined,
): ((orgId: string, sessionId: string) => Promise<boolean>) | null {
  if (roles?.isShared === undefined) return null;
  return (orgId, sessionId) =>
    roles.isShared?.({ orgId, sessionId }) ?? Promise.resolve(true);
}
