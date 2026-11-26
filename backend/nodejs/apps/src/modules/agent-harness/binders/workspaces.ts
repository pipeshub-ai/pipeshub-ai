import { newObjectId } from '../../../libs/repositories/ids';
import { capabilityKnown } from '../domain/capabilities';
import type { StoreSession } from '../../../libs/repositories/agent-harness/durable-transaction';
import { ChainWorkspaceDownloadAudit } from '../adapters/chain-workspace-download-audit';
import { inDurableTransaction } from '../../../libs/repositories/agent-harness/durable-transaction';
import { createSessionRunLookup } from '../../../libs/repositories/agent-harness/session-erasure';
import {
  createWorkspaceKeyStore,
  createWorkspaceRepository,
  createWorkspaceWriterStore,
} from '../../../libs/repositories/agent-harness/workspace';
import { ExecutionService } from '../application/execution-service';
import { SessionForkService } from '../application/session-fork-service';
import { SessionWorkspaceKeys } from '../application/session-workspace-keys';
import { SessionWorkspaceService } from '../application/session-workspace-service';
import { SharedArtifactService } from '../application/shared-artifacts';
import { SharedCitationService } from '../application/shared-citations';
import { WorkspaceObjectResealer } from '../application/workspace-object-resealer';
import { WorkspaceService } from '../application/workspace-service';
import { SessionParticipantRecorder } from '../ports/session-participants';
import { SessionSandboxService } from '../application/session-sandbox-service';
import type { ApprovalRulesReader } from '../application/deployment-approval-rules';
import { EngineSignInService } from '../application/engine-signin-service';
import { HarnessCapabilityService } from '../application/capability-service';
import { CodingEngineService } from '../application/coding-engine-service';
import { SessionRepositoryService } from '../application/session-repository-service';
import { sessionPullRequests } from '../application/session-pull-requests';
import type { ExecutionStore } from '../ports/execution-store';
import { boundOrNull, HarnessBinder, HarnessBindingContext } from './context';
import {
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_EXECUTION_STORE,
  AGENT_HARNESS_SESSION_FORKS,
  AGENT_HARNESS_SESSION_PARTICIPANTS,
  AGENT_HARNESS_APPROVAL_SETTINGS,
  AGENT_HARNESS_CAPABILITIES,
  AGENT_HARNESS_CODING_ENGINES,
  AGENT_HARNESS_ENGINE_SIGNINS,
  AGENT_HARNESS_SESSION_REPOSITORIES,
  AGENT_HARNESS_SESSION_SANDBOX,
  AGENT_HARNESS_SESSION_WORKSPACES,
  AGENT_HARNESS_SHARED_ARTIFACTS,
  AGENT_HARNESS_SHARED_CITATIONS,
  AGENT_HARNESS_WORKSPACES,
} from './tokens';

export const workspaceKeys = ({
  journal,
  logger,
}: Pick<HarnessBindingContext, 'journal' | 'logger'>): SessionWorkspaceKeys =>
  new SessionWorkspaceKeys({
    store: createWorkspaceKeyStore(),
    cipher: journal.cipher,
    logger,
  });

export const bindWorkspaces: HarnessBinder = (binding) => {
  const { container, journal, logger } = binding;
  if (container.isBound(AGENT_HARNESS_WORKSPACES)) return;
  container
    .bind<WorkspaceService>(AGENT_HARNESS_WORKSPACES)
    .toDynamicValue(
      (context) =>
        new WorkspaceService({
          runs: context.container.get<ExecutionService>(
            AGENT_HARNESS_EXECUTION_SERVICE,
          ),
          repository: createWorkspaceRepository(),
          cipher: journal.cipher,
          keys: workspaceKeys(binding),
          writers: createWorkspaceWriterStore(),
          logger,
        }),
    )
    .inSingletonScope();
};

export const bindSessionForks: HarnessBinder = (binding) => {
  const { container, dependencies, journal, logger } = binding;
  const forks = dependencies.forks;
  if (forks === undefined || container.isBound(AGENT_HARNESS_SESSION_FORKS)) {
    return;
  }
  container
    .bind<SessionForkService<StoreSession>>(AGENT_HARNESS_SESSION_FORKS)
    .toDynamicValue(
      (context) =>
        new SessionForkService<StoreSession>({
          store: forks.store,
          runs: createSessionRunLookup(),
          journal: journal.store,
          recorder: context.container.get<
            SessionParticipantRecorder<StoreSession>
          >(AGENT_HARNESS_SESSION_PARTICIPANTS),
          citations: context.container.get<SharedCitationService>(
            AGENT_HARNESS_SHARED_CITATIONS,
          ),
          artifacts: context.container.get<SharedArtifactService>(
            AGENT_HARNESS_SHARED_ARTIFACTS,
          ),
          copier: forks.artifacts,
          workspaces: {
            service: context.container.get<WorkspaceService>(
              AGENT_HARNESS_WORKSPACES,
            ),
            objects: new WorkspaceObjectResealer(
              workspaceKeys(binding),
              forks.objects,
            ),
          },
          transaction: inDurableTransaction,
          newSessionId: newObjectId,
          ...(logger !== undefined ? { logger } : {}),
        }),
    )
    .inSingletonScope();
};

export const bindSessionWorkspaces: HarnessBinder = (binding) => {
  const { container, dependencies, journal } = binding;
  if (container.isBound(AGENT_HARNESS_SESSION_WORKSPACES)) return;
  const objects = dependencies.forks?.objects;
  const files =
    objects !== undefined
      ? new WorkspaceObjectResealer(workspaceKeys(binding), objects)
      : undefined;
  container
    .bind<SessionWorkspaceService>(AGENT_HARNESS_SESSION_WORKSPACES)
    .toDynamicValue(
      (context) =>
        new SessionWorkspaceService(
          dependencies.sessions,
          context.container.get<WorkspaceService>(AGENT_HARNESS_WORKSPACES),
          files !== undefined
            ? (orgId, sessionId) => files.reader(orgId, sessionId)
            : undefined,
          new ChainWorkspaceDownloadAudit<StoreSession>(
            journal.audit,
            inDurableTransaction,
          ).record,
          files !== undefined
            ? (orgId, sessionId) => files.writer(orgId, sessionId)
            : undefined,
        ),
    )
    .inSingletonScope();
};

/** `/sandbox` (SLASH3 SB1): composed from what Node holds; nothing here starts a sandbox. */
export const bindSessionSandbox: HarnessBinder = ({
  container,
  dependencies,
  logger,
}) => {
  if (container.isBound(AGENT_HARNESS_SESSION_SANDBOX)) return;
  container
    .bind<SessionSandboxService>(AGENT_HARNESS_SESSION_SANDBOX)
    .toDynamicValue((context) => {
      const approvals = boundOrNull<ApprovalRulesReader>(
        context.container,
        AGENT_HARNESS_APPROVAL_SETTINGS,
      );
      const signIns = boundOrNull<EngineSignInService>(
        context.container,
        AGENT_HARNESS_ENGINE_SIGNINS,
      );
      const capabilities = boundOrNull<HarnessCapabilityService>(
        context.container,
        AGENT_HARNESS_CAPABILITIES,
      );
      const codingEngines = boundOrNull<CodingEngineService>(
        context.container,
        AGENT_HARNESS_CODING_ENGINES,
      );
      return new SessionSandboxService({
        sessions: dependencies.sessions,
        workspaces: context.container.get<WorkspaceService>(
          AGENT_HARNESS_WORKSPACES,
        ),
        ...(approvals !== null ? { approvals } : {}),
        ...(signIns !== null ? { signIns } : {}),
        ...(codingEngines !== null ? { codingEngines } : {}),
        ...(capabilities !== null
          ? {
              // Unknown is not none (ADR-CAP-01): a round that could not ask the broker answers null.
              isolation: () => {
                const snapshot = capabilities.current();
                return capabilityKnown(snapshot, 'sandbox.isolation')
                  ? (snapshot?.capabilities['sandbox.isolation'] ?? null)
                  : null;
              },
            }
          : {}),
        ...(logger !== undefined ? { logger } : {}),
      });
    })
    .inSingletonScope();
};

/** GIT-FLOW (WP-26): a chat's repository, where its coding engine, the preference store and the toolset store are. */
export const bindSessionRepositories: HarnessBinder = ({
  container,
  dependencies,
  controlPlane,
  journal,
  logger,
}) => {
  const preferences = dependencies.codingEnginePreferences;
  const toolsets = dependencies.repositoryToolsets;
  if (
    preferences === undefined ||
    toolsets === undefined ||
    !container.isBound(AGENT_HARNESS_CODING_ENGINES) ||
    container.isBound(AGENT_HARNESS_SESSION_REPOSITORIES)
  ) {
    return;
  }
  container
    .bind<SessionRepositoryService>(AGENT_HARNESS_SESSION_REPOSITORIES)
    .toDynamicValue(
      (context) =>
        new SessionRepositoryService({
          preferences,
          transaction: controlPlane,
          toolsets,
          workspaces: context.container.get<WorkspaceService>(
            AGENT_HARNESS_WORKSPACES,
          ),
          engines: context.container.get<CodingEngineService>(
            AGENT_HARNESS_CODING_ENGINES,
          ),
          pullRequests: sessionPullRequests({
            runs: context.container.get<ExecutionStore>(
              AGENT_HARNESS_EXECUTION_STORE,
            ),
            journal: journal.runJournal,
          }),
          ...(logger !== undefined ? { logger } : {}),
        }),
    )
    .inSingletonScope();
};
