/* eslint-disable @typescript-eslint/unbound-method -- AuthMiddleware handlers follow the repository's Express middleware contract and are registered by reference. */
import { RequestHandler, Router } from 'express';
import { scanAfterAuthentication } from './pasted-secret-guard';
import { createKeyedRateLimiter } from '../../../libs/middlewares/rate-limit.middleware';
import { Logger } from '../../../libs/services/logger.service';
import multer from 'multer';
import {
  createMulter,
  requestDiskStorage,
} from '../../../libs/utils/multer.utils';
import {
  BadRequestError,
  ExpectedRefusalError,
  TooManyRequestsError,
} from '../../../libs/errors/http.errors';
import type { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { ERROR_CODES } from '../contracts/generated/ext/pipeshub/errors';
import { harnessWireCode } from '../domain/errors';
import { UPLOAD_MAX_FILES } from '../application/session-workspace-service';
import { Container } from 'inversify';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { SessionRunService } from '../application/session-run-service';
import { SessionInteractionService } from '../application/session-interactions';
import { RunAdmissionService } from '../application/run-admission-service';
import { RunTimelineService } from '../application/run-timeline-service';
import { SessionWorkspaceService } from '../application/session-workspace-service';
import { SessionSandboxService } from '../application/session-sandbox-service';
import { CodingEngineService } from '../application/coding-engine-service';
import { SessionRepositoryService } from '../application/session-repository-service';
import { RunActivityService } from '../application/run-activity-service';
import { AgentAccessPolicy } from '../ports/agent-access';
import {
  AGENT_HARNESS_RUN_ACTIVITY,
  AGENT_HARNESS_RUN_FOLLOWUPS,
  AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  AGENT_HARNESS_RUN_STREAMS,
  AGENT_HARNESS_RUN_WATCHERS,
  AGENT_HARNESS_RUN_TIMELINES,
  AGENT_HARNESS_SESSION_INTERACTIONS,
  AGENT_HARNESS_SESSION_RUN_SERVICE,
  AGENT_HARNESS_AGENT_ACCESS,
  AGENT_HARNESS_CODING_ENGINES,
  AGENT_HARNESS_SESSION_REPOSITORIES,
  AGENT_HARNESS_SESSION_SANDBOX,
  AGENT_HARNESS_SESSION_WORKSPACES,
} from '../composition';
import { RunStreamService } from '../projection/stream-service';
import { RunStreamController } from './stream-controller';
import type { RunWatchRegistry } from '../application/run-end-notices';
import { RunActivityController } from './activity-controller';
import { RunFollowupsController } from './followups-controller';
import { RunFollowupsService } from '../application/run-followups-service';
import { AgentHarnessSessionController } from './session-controller';
import {
  activityStepSchema,
  chatActivityStepSchema,
  chatCommandLogSchema,
  chatSessionUsageSchema,
  compactChatSessionSchema,
  compactSessionSchema,
  commandLogSchema,
  controlChatSessionRunSchema,
  controlSessionRunSchema,
  decideChatSessionInteractionSchema,
  decideSessionInteractionSchema,
  decideChatSessionInteractionsSchema,
  decideSessionInteractionsSchema,
  createChatSessionRunSchema,
  createSessionRunSchema,
  getChatSessionRunSchema,
  getSessionRunSchema,
  listChatSessionWorkspaceVersionsSchema,
  listSessionWorkspaceVersionsSchema,
  restoreChatSessionWorkspaceVersionSchema,
  restoreSessionWorkspaceVersionSchema,
  sessionWorkspaceDiffSchema,
  chatSessionWorkspaceDiffSchema,
  sessionWorkspaceFilesSchema,
  chatSessionWorkspaceFilesSchema,
  sessionWorkspaceFileSchema,
  chatSessionWorkspaceFileSchema,
  sessionWorkspaceArchiveSchema,
  chatSessionWorkspaceArchiveSchema,
  sessionSandboxSchema,
  chatSessionSandboxSchema,
  runSessionCommandSchema,
  runChatSessionCommandSchema,
  putSessionCodingEngineSchema,
  putChatSessionCodingEngineSchema,
  putSessionRepositorySchema,
  putChatSessionRepositorySchema,
  uploadSessionFilesSchema,
  uploadChatSessionFilesSchema,
  UPLOAD_MAX_FILE_BYTES,
  UPLOAD_MAX_REQUEST_BYTES,
  streamChatSessionRunSchema,
  streamSessionRunSchema,
  runFollowupsSchema,
  sessionUsageSchema,
  chatRunFollowupsSchema,
  submitChatSessionRunSchema,
  submitSessionRunSchema,
  markChatSessionSeenSchema,
  markSessionSeenSchema,
  sessionGrantsSchema,
  chatSessionGrantsSchema,
  revokeSessionGrantSchema,
  revokeChatSessionGrantSchema,
} from './schemas';
import { BackgroundAgentController } from './background-agent-controller';

type HarnessSessionKind = 'agent' | 'chat';

const tooLarge = (message: string): ExpectedRefusalError =>
  new ExpectedRefusalError(
    harnessWireCode('WORKSPACE_TOO_LARGE'),
    message,
    ERROR_CODES.WORKSPACE_TOO_LARGE.status,
  );

/**
 * SLASH3 `/upload`, SEC-C-08: the request's bytes are counted as they arrive, declared or chunked, and it is
 * refused past 512 MiB; each file past 100 MiB, or past the file count, is refused as it is read. Files stream
 * to a private temporary folder, never to memory, and are read one at a time when they are written.
 */
const uploadFiles = createMulter({
  storage: requestDiskStorage(),
  limits: { fileSize: UPLOAD_MAX_FILE_BYTES, files: UPLOAD_MAX_FILES },
  maxRequestBytes: UPLOAD_MAX_REQUEST_BYTES,
  tooLarge: () => tooLarge('The upload is larger than 512 MiB'),
}).array('files', UPLOAD_MAX_FILES);

/** Uploads one person may stream at once; the next waits for one to finish (SEC-C-08). */
export const UPLOADS_IN_FLIGHT_PER_PERSON = 2;

/**
 * shortcut: in-process, like the `/run` limiter below, so N Node replicas admit N x 2; the 512 MiB request
 * cap still bounds each one. Move to a shared store with them.
 */
const uploadsInFlight = new Map<string, number>();

const uploadBody: RequestHandler = (req, res, next) => {
  const user = (req as AuthenticatedUserRequest).user;
  const who = `${String(user?.orgId)}:${String(user?.userId)}`;
  const running = uploadsInFlight.get(who) ?? 0;
  if (running >= UPLOADS_IN_FLIGHT_PER_PERSON) {
    res.setHeader('Connection', 'close');
    next(
      new TooManyRequestsError(
        'Another upload of yours is still being received. Wait for it to finish, then try again.',
      ),
    );
    return;
  }
  uploadsInFlight.set(who, running + 1);
  res.once('close', () => {
    const left = (uploadsInFlight.get(who) ?? 1) - 1;
    if (left <= 0) uploadsInFlight.delete(who);
    else uploadsInFlight.set(who, left);
  });
  void uploadFiles(req, res, (error: unknown) => {
    if (error instanceof multer.MulterError) {
      next(
        error.code === 'LIMIT_FILE_SIZE' || error.code === 'LIMIT_FILE_COUNT'
          ? tooLarge(
              'A file is larger than 100 MiB, or there are too many files',
            )
          : new BadRequestError(error.message),
      );
      return;
    }
    next(error as Error | undefined);
  });
};

/** SLASH3 §3.5: `/run` per person per minute, on top of the admission quotas every run fits. */
export const SANDBOX_COMMANDS_PER_MINUTE = 20;

/**
 * shortcut: the limiter's store is in-process (as the skills import's), so N Node replicas admit N×20 a minute;
 * the admission quotas (30 runs a minute per person, shared) still bound it. Move to a shared store with them.
 */
function commandRateLimit(container: Container): RequestHandler[] {
  if (!container.isBound('Logger')) return [];
  return [
    createKeyedRateLimiter(container.get<Logger>('Logger'), {
      prefix: 'cmd',
      maxRequestsPerMinute: SANDBOX_COMMANDS_PER_MINUTE,
      message: 'Too many /run commands. Please wait a moment and try again.',
    }),
  ];
}

/** Failed authentications (401) one address may send these routes a minute; nothing else is counted. */
export const SESSION_AUTH_FAILURES_PER_MINUTE = 120;

/**
 * Every session route authenticates first, so an address whose tokens keep
 * being refused (guessing one) is slowed here, beside the app-wide limit. A
 * signed-in person is never counted, whatever the route answers them.
 */
export function sessionAuthFailureRateLimit(
  container: Container,
  kind: HarnessSessionKind,
): RequestHandler {
  const logger = container.isBound('Logger')
    ? container.get<Logger>('Logger')
    : Logger.getInstance({ service: 'AgentHarnessSessions' });
  return createKeyedRateLimiter(logger, {
    prefix: `harness-${kind}-auth-failures`,
    maxRequestsPerMinute: SESSION_AUTH_FAILURES_PER_MINUTE,
    message: 'Too many failed sign-ins. Please wait a moment and try again.',
    countOnlyFailures: true,
    isFailure: (_req, res) => res.statusCode === 401,
  });
}

function createHarnessSessionRouter(
  container: Container,
  kind: HarnessSessionKind,
): Router {
  const router = Router();
  router.use(sessionAuthFailureRateLimit(container, kind));
  // SEC-CR-07: a pasted secret never reaches validation, admission or storage; SEC-C-01: nor is an anonymous body scanned.
  const auth = scanAfterAuthentication(
    container.get<AuthMiddleware>('AuthMiddleware'),
  );
  const runs = container.get<SessionRunService>(
    AGENT_HARNESS_SESSION_RUN_SERVICE,
  );
  const admissions = container.get<RunAdmissionService>(
    AGENT_HARNESS_RUN_ADMISSION_SERVICE,
  );
  const controller = new AgentHarnessSessionController(
    runs,
    admissions,
    kind,
    container.isBound(AGENT_HARNESS_SESSION_INTERACTIONS)
      ? container.get<SessionInteractionService>(
          AGENT_HARNESS_SESSION_INTERACTIONS,
        )
      : undefined,
    {
      ...(container.isBound(AGENT_HARNESS_RUN_TIMELINES)
        ? {
            timelines: container.get<RunTimelineService>(
              AGENT_HARNESS_RUN_TIMELINES,
            ),
          }
        : {}),
      ...(container.isBound(AGENT_HARNESS_SESSION_WORKSPACES)
        ? {
            workspaces: container.get<SessionWorkspaceService>(
              AGENT_HARNESS_SESSION_WORKSPACES,
            ),
          }
        : {}),
      ...(container.isBound(AGENT_HARNESS_SESSION_SANDBOX)
        ? {
            sandbox: container.get<SessionSandboxService>(
              AGENT_HARNESS_SESSION_SANDBOX,
            ),
          }
        : {}),
      ...(container.isBound(AGENT_HARNESS_CODING_ENGINES)
        ? {
            codingEngines: container.get<CodingEngineService>(
              AGENT_HARNESS_CODING_ENGINES,
            ),
          }
        : {}),
      ...(container.isBound(AGENT_HARNESS_SESSION_REPOSITORIES)
        ? {
            repositories: container.get<SessionRepositoryService>(
              AGENT_HARNESS_SESSION_REPOSITORIES,
            ),
          }
        : {}),
      ...(container.isBound(AGENT_HARNESS_AGENT_ACCESS)
        ? {
            agentAccess: container.get<AgentAccessPolicy>(
              AGENT_HARNESS_AGENT_ACCESS,
            ),
          }
        : {}),
      ...(container.isBound('Logger')
        ? { logger: container.get<Logger>('Logger') }
        : {}),
    },
  );

  const isAgent = kind === 'agent';
  const executeScope = isAgent
    ? OAuthScopeNames.AGENT_EXECUTE
    : OAuthScopeNames.CONVERSATION_CHAT;
  const readScope = isAgent
    ? OAuthScopeNames.AGENT_READ
    : OAuthScopeNames.CONVERSATION_READ;
  const createPath = isAgent ? '/:agentKey/runs' : '/runs';
  const sessionPath = isAgent
    ? '/:agentKey/conversations/:sessionId'
    : '/:sessionId';

  router.post(
    createPath,
    auth.authenticate,
    requireScopes(executeScope),
    ValidationMiddleware.validate(
      isAgent ? createSessionRunSchema : createChatSessionRunSchema,
      {
        stripUnknown: true,
      },
    ),
    controller.createSessionRun,
  );
  router.post(
    `${sessionPath}/runs`,
    auth.authenticate,
    requireScopes(executeScope),
    ValidationMiddleware.validate(
      isAgent ? submitSessionRunSchema : submitChatSessionRunSchema,
      {
        stripUnknown: true,
      },
    ),
    controller.submitSessionRun,
  );

  router.get(
    `${sessionPath}/runs/:runId`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(
      isAgent ? getSessionRunSchema : getChatSessionRunSchema,
      { stripUnknown: true },
    ),
    controller.get,
  );
  router.get(
    `${sessionPath}/runs/:runId/resolved`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(
      isAgent ? getSessionRunSchema : getChatSessionRunSchema,
      { stripUnknown: true },
    ),
    controller.resolved,
  );
  if (container.isBound(AGENT_HARNESS_RUN_STREAMS)) {
    const streams = new RunStreamController(
      runs,
      container.get<RunStreamService>(AGENT_HARNESS_RUN_STREAMS),
      kind,
      container.isBound(AGENT_HARNESS_RUN_WATCHERS)
        ? container.get<RunWatchRegistry>(AGENT_HARNESS_RUN_WATCHERS)
        : undefined,
    );
    router.get(
      `${sessionPath}/runs/:runId/stream`,
      auth.authenticate,
      requireScopes(readScope),
      ValidationMiddleware.validate(
        isAgent ? streamSessionRunSchema : streamChatSessionRunSchema,
        { stripUnknown: true },
      ),
      streams.stream,
    );
  }

  router.post(
    `${sessionPath}/runs/:runId/cancel`,
    auth.authenticate,
    requireScopes(executeScope),
    ValidationMiddleware.validate(
      isAgent ? controlSessionRunSchema : controlChatSessionRunSchema,
      { stripUnknown: true },
    ),
    controller.cancel,
  );
  router.post(
    `${sessionPath}/runs/:runId/resume`,
    auth.authenticate,
    requireScopes(executeScope),
    ValidationMiddleware.validate(
      isAgent ? controlSessionRunSchema : controlChatSessionRunSchema,
      { stripUnknown: true },
    ),
    controller.resume,
  );
  const runParams = isAgent ? getSessionRunSchema : getChatSessionRunSchema;
  router.get(
    `${sessionPath}/runs/:runId/timeline`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(runParams, { stripUnknown: true }),
    controller.timeline,
  );
  router.get(
    `${sessionPath}/runs/:runId/cost`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(runParams, { stripUnknown: true }),
    controller.cost,
  );
  if (container.isBound(AGENT_HARNESS_RUN_ACTIVITY)) {
    const activity = new RunActivityController(
      container.get<RunActivityService>(AGENT_HARNESS_RUN_ACTIVITY),
      kind,
    );
    router.get(
      `${sessionPath}/runs/:runId/activity`,
      auth.authenticate,
      requireScopes(readScope),
      ValidationMiddleware.validate(runParams, { stripUnknown: true }),
      activity.stream,
    );
    router.get(
      `${sessionPath}/runs/:runId/commands/:commandId/log`,
      auth.authenticate,
      requireScopes(readScope),
      ValidationMiddleware.validate(
        isAgent ? commandLogSchema : chatCommandLogSchema,
        { stripUnknown: true },
      ),
      activity.log,
    );
    router.get(
      `${sessionPath}/runs/:runId/steps/:toolCallId`,
      auth.authenticate,
      requireScopes(readScope),
      ValidationMiddleware.validate(
        isAgent ? activityStepSchema : chatActivityStepSchema,
        { stripUnknown: true },
      ),
      activity.step,
    );
  }
  if (container.isBound(AGENT_HARNESS_RUN_FOLLOWUPS)) {
    const followups = new RunFollowupsController(
      container.get<RunFollowupsService>(AGENT_HARNESS_RUN_FOLLOWUPS),
      kind,
    );
    router.get(
      `${sessionPath}/runs/:runId/followups`,
      auth.authenticate,
      requireScopes(readScope),
      ValidationMiddleware.validate(
        isAgent ? runFollowupsSchema : chatRunFollowupsSchema,
        { stripUnknown: true },
      ),
      followups.read,
    );
  }
  router.get(
    `${sessionPath}/usage`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(
      isAgent ? sessionUsageSchema : chatSessionUsageSchema,
      { stripUnknown: true },
    ),
    controller.usage,
  );
  router.post(
    `${sessionPath}/compact`,
    auth.authenticate,
    requireScopes(executeScope),
    ValidationMiddleware.validate(
      isAgent ? compactSessionSchema : compactChatSessionSchema,
      { stripUnknown: true },
    ),
    controller.compact,
  );
  router.get(
    `${sessionPath}/runs/:runId/interactions/authority`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(runParams, { stripUnknown: true }),
    controller.authority,
  );
  router.get(
    `${sessionPath}/workspace/versions`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(
      isAgent
        ? listSessionWorkspaceVersionsSchema
        : listChatSessionWorkspaceVersionsSchema,
      { stripUnknown: true },
    ),
    controller.versions,
  );
  router.get(
    `${sessionPath}/workspace/diff`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(
      isAgent ? sessionWorkspaceDiffSchema : chatSessionWorkspaceDiffSchema,
      { stripUnknown: true },
    ),
    controller.diff,
  );
  router.get(
    `${sessionPath}/workspace/files`,
    auth.authenticate,
    requireScopes(readScope),
    controller.requireOwner,
    ValidationMiddleware.validate(
      isAgent ? sessionWorkspaceFilesSchema : chatSessionWorkspaceFilesSchema,
      { stripUnknown: true },
    ),
    controller.files,
  );
  router.post(
    `${sessionPath}/workspace/files`,
    auth.authenticate,
    requireScopes(executeScope),
    controller.requireOwner,
    ValidationMiddleware.validate(
      isAgent ? uploadSessionFilesSchema : uploadChatSessionFilesSchema,
      { stripUnknown: true },
    ),
    uploadBody,
    controller.upload,
  );
  router.get(
    `${sessionPath}/workspace/file`,
    auth.authenticate,
    requireScopes(readScope),
    controller.requireOwner,
    ValidationMiddleware.validate(
      isAgent ? sessionWorkspaceFileSchema : chatSessionWorkspaceFileSchema,
      { stripUnknown: true },
    ),
    controller.file,
  );
  router.get(
    `${sessionPath}/workspace/archive`,
    auth.authenticate,
    requireScopes(readScope),
    controller.requireOwner,
    ValidationMiddleware.validate(
      isAgent
        ? sessionWorkspaceArchiveSchema
        : chatSessionWorkspaceArchiveSchema,
      { stripUnknown: true },
    ),
    controller.archive,
  );
  router.post(
    `${sessionPath}/commands/run`,
    auth.authenticate,
    requireScopes(executeScope),
    controller.requireOwner,
    ...commandRateLimit(container),
    ValidationMiddleware.validate(
      isAgent ? runSessionCommandSchema : runChatSessionCommandSchema,
      { stripUnknown: true },
    ),
    controller.runCommand,
  );
  router.put(
    `${sessionPath}/coding-engine`,
    auth.authenticate,
    requireScopes(executeScope),
    controller.requireOwner,
    ValidationMiddleware.validate(
      isAgent ? putSessionCodingEngineSchema : putChatSessionCodingEngineSchema,
      { stripUnknown: true },
    ),
    controller.setCodingEngine,
  );
  // GIT-FLOW (WP-26): the chat's GitHub repository, beside its coding engine; the owner's alone.
  router.get(
    `${sessionPath}/repository`,
    auth.authenticate,
    requireScopes(readScope),
    controller.requireOwner,
    ValidationMiddleware.validate(
      isAgent ? sessionSandboxSchema : chatSessionSandboxSchema,
      { stripUnknown: true },
    ),
    controller.repository,
  );
  router.put(
    `${sessionPath}/repository`,
    auth.authenticate,
    requireScopes(executeScope),
    controller.requireOwner,
    ValidationMiddleware.validate(
      isAgent ? putSessionRepositorySchema : putChatSessionRepositorySchema,
      { stripUnknown: true },
    ),
    controller.setRepository,
  );
  router.delete(
    `${sessionPath}/repository`,
    auth.authenticate,
    requireScopes(executeScope),
    controller.requireOwner,
    ValidationMiddleware.validate(
      isAgent ? sessionSandboxSchema : chatSessionSandboxSchema,
      { stripUnknown: true },
    ),
    controller.clearRepository,
  );
  router.get(
    `${sessionPath}/sandbox`,
    auth.authenticate,
    requireScopes(readScope),
    controller.requireOwner,
    ValidationMiddleware.validate(
      isAgent ? sessionSandboxSchema : chatSessionSandboxSchema,
      { stripUnknown: true },
    ),
    controller.sandbox,
  );
  router.delete(
    `${sessionPath}/sandbox/kept`,
    auth.authenticate,
    requireScopes(executeScope),
    controller.requireOwner,
    ValidationMiddleware.validate(
      isAgent ? sessionSandboxSchema : chatSessionSandboxSchema,
      { stripUnknown: true },
    ),
    controller.endKeptSandbox,
  );
  router.post(
    `${sessionPath}/workspace/versions/:revision/restore`,
    auth.authenticate,
    requireScopes(executeScope),
    ValidationMiddleware.validate(
      isAgent
        ? restoreSessionWorkspaceVersionSchema
        : restoreChatSessionWorkspaceVersionSchema,
      { stripUnknown: true },
    ),
    controller.restore,
  );
  router.post(
    `${sessionPath}/runs/:runId/interactions/:interactionId/decision`,
    auth.authenticate,
    requireScopes(executeScope),
    ValidationMiddleware.validate(
      isAgent
        ? decideSessionInteractionSchema
        : decideChatSessionInteractionSchema,
      { stripUnknown: true },
    ),
    controller.decide,
  );
  router.post(
    `${sessionPath}/runs/:runId/interactions/decisions`,
    auth.authenticate,
    requireScopes(executeScope),
    ValidationMiddleware.validate(
      isAgent
        ? decideSessionInteractionsSchema
        : decideChatSessionInteractionsSchema,
      { stripUnknown: true },
    ),
    controller.decideAll,
  );

  // UI-B1 (ADR-UI-01, ADR-GRANT-01 D8): a job's brief, the caller's seen mark, and the conversation's grants.
  const background = new BackgroundAgentController(container, kind);
  router.get(
    `${sessionPath}/runs/:runId/brief`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(runParams, { stripUnknown: true }),
    background.brief,
  );
  // UI-B5 (ADR-UI-01 D5, D6): how a job's goal changed, and one request's jobs ("N of M back").
  router.get(
    `${sessionPath}/runs/:runId/brief/history`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(runParams, { stripUnknown: true }),
    background.briefHistory,
  );
  router.get(
    `${sessionPath}/runs/:runId/jobs`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(runParams, { stripUnknown: true }),
    background.requestJobs,
  );
  router.put(
    `${sessionPath}/seen`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(
      isAgent ? markSessionSeenSchema : markChatSessionSeenSchema,
    ),
    background.seen,
  );
  router.get(
    `${sessionPath}/grants`,
    auth.authenticate,
    requireScopes(readScope),
    ValidationMiddleware.validate(
      isAgent ? sessionGrantsSchema : chatSessionGrantsSchema,
      { stripUnknown: true },
    ),
    background.grants,
  );
  router.delete(
    `${sessionPath}/grants/:grantId`,
    auth.authenticate,
    requireScopes(executeScope),
    ValidationMiddleware.validate(
      isAgent ? revokeSessionGrantSchema : revokeChatSessionGrantSchema,
      { stripUnknown: true },
    ),
    background.revokeGrant,
  );

  return router;
}

export function createAgentHarnessSessionRouter(container: Container): Router {
  return createHarnessSessionRouter(container, 'agent');
}

export function createChatHarnessSessionRouter(container: Container): Router {
  return createHarnessSessionRouter(container, 'chat');
}
