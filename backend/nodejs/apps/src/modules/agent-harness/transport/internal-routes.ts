import { harnessEditionOf, mountEditionRoutes } from '../edition';
import { Router } from 'express';
import { createKeyedRateLimiter } from '../../../libs/middlewares/rate-limit.middleware';
import { Logger } from '../../../libs/services/logger.service';
import { Container } from 'inversify';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { ExecutionService } from '../application/execution-service';
import { HarnessAuthority } from '../application/harness-authority';
import { ChildRunService } from '../application/child-run-service';
import { AgentHarnessChildController } from './child-controller';
import { AgentHarnessBudgetController } from './budget-controller';
import { BudgetLedgerService } from '../application/budget-ledger-service';
import {
  AGENT_HARNESS_AUTHORITY,
  AGENT_HARNESS_BUDGET_LEDGER,
  AGENT_HARNESS_CHILD_RUNS,
  AGENT_HARNESS_EXECUTION_SERVICE,
  AGENT_HARNESS_GATEWAY_TOKENS,
  AGENT_HARNESS_MEMORIES,
  AGENT_HARNESS_RUN_CONTENT,
  AGENT_HARNESS_WORKSPACES,
} from '../composition';
import { WorkspaceService } from '../application/workspace-service';
import { AgentHarnessWorkspaceController } from './workspace-controller';
import { RunContentService } from '../application/run-content-service';
import {
  AgentHarnessContentController,
  rawContentBody,
} from './content-controller';
import {
  requireAttemptToken,
  requireRoleCredential,
  requireWorkerCredential,
  editionJsonBodies,
  runTokenRoute,
  thenJson,
} from './internal-auth';
import { AgentHarnessGatewayTokenController } from './gateway-token-controller';
import { GatewayTokenService } from '../application/gateway-token-service';
import { AgentHarnessInternalController } from './internal-controller';
import { AgentHarnessFollowupsController } from './followups-controller';
import { HarnessLogger } from '../application/harness-log';
import { LiveLayerService } from '../application/live-layer-service';
import { AGENT_HARNESS_LIVE_LAYERS } from '../binders/change-notifier';
import { AgentHarnessLiveLayerController } from './live-layer-controller';
import { MemoryService } from '../application/memory-service';
import { AgentHarnessMemoryController } from './memory-controller';
import { AgentHarnessChatScheduleController } from './chat-schedule-controller';
import type { ChatScheduleService } from '../application/chat-schedules';
import {
  AGENT_HARNESS_CHAT_SCHEDULES,
  AGENT_HARNESS_CLAIM_FEATURES,
  AGENT_HARNESS_METRICS_TARGETS,
  AGENT_HARNESS_LOCAL_METRICS,
} from '../binders/tokens';
import {
  AgentHarnessInternalMetricsController,
  DEFAULT_METRICS_TARGETS,
  MetricsTargets,
} from './internal-metrics-controller';
import type { ClaimFeatureSource } from '../ports/claim-features';
import type { LocalMetrics } from '../application/harness-local-metrics';
import { advertisedNodeCapabilities } from '../domain/node-capabilities';
import { recordedRunFeatures } from '../domain/recorded-run-fields';
import type { NodeCapabilities } from '../contracts/generated/core/node-capabilities';
import {
  claimNextRunSchema,
  claimRunSchema,
  commitRunSchema,
  readRunAuditSchema,
  readRunContentSchema,
  readLiveLayerSchema,
  readRunJournalSchema,
  releaseRunSchema,
  renewRunSchema,
  spawnChildRunSchema,
} from './schemas';

/**
 * Harness worker routes (REPORT 6.1.8). Claim takes the per-deployment worker
 * credential; every later call takes the attempt token the claim returned.
 * Scoped JWTs are not accepted here, whatever their audience or scopes.
 */
/**
 * Where the app mounts the worker-only routes. Only the worker credential or an
 * attempt token reaches them, and they carry model and tool output verbatim, so
 * the global XSS middleware exempts this prefix (see xss-sanitization.middleware).
 */
export const AGENT_HARNESS_INTERNAL_PREFIX = '/api/v1/agent-harness/internal';

/** Schedule requests one run may make a minute (ADR-SCH-01 D3); the worker's own address and the run key the count. */
export const CHAT_SCHEDULES_PER_RUN_PER_MINUTE = 30;

/** What this Node names to its workers: its own features and those of an edition's bound services. */
export function nodeCapabilitiesOf(
  container: Container,
): Readonly<NodeCapabilities> {
  const services = container.isBound(AGENT_HARNESS_CLAIM_FEATURES)
    ? container.get<ClaimFeatureSource>(AGENT_HARNESS_CLAIM_FEATURES).features()
    : [];
  return advertisedNodeCapabilities({
    edition: [...recordedRunFeatures(), ...services],
  });
}

export function createAgentHarnessInternalRouter(container: Container): Router {
  const router = Router();
  const authority = container.get<HarnessAuthority>(AGENT_HARNESS_AUTHORITY);
  const executions = container.get<ExecutionService>(
    AGENT_HARNESS_EXECUTION_SERVICE,
  );
  const logger = container.isBound('Logger')
    ? container.get<HarnessLogger>('Logger')
    : undefined;
  const controller = new AgentHarnessInternalController(
    executions,
    authority,
    undefined,
    logger,
    undefined,
    nodeCapabilitiesOf(container),
  );
  // SEC-C-09: each guard brings its route's JSON parser after it; the app parses nothing up front.
  const worker = thenJson(
    requireWorkerCredential(authority, logger),
    'worker-credential',
  );
  const attempt = thenJson(
    requireAttemptToken(authority, logger),
    'attempt-token',
  );
  // SEC-C-10 (ADR-CRED-01): each machine role only where it calls.
  const scraper = thenJson(
    requireRoleCredential(authority, ['scrape'], logger),
    'worker-credential',
  );
  const egressProxy = thenJson(
    requireRoleCredential(authority, ['egress-proxy'], logger),
    'worker-credential',
  );

  // C9.04: the internal-only services' metrics (egress proxy, Model and Tool Gateways, sandbox broker) and Node's own
  // harness families and run state in one exposition, for the deployment's scraper; each service is asked with the
  // scraper's credential too. No series carries an org id.
  const metrics = new AgentHarnessInternalMetricsController(
    () => authority.credentialFor('scrape'),
    container.isBound(AGENT_HARNESS_METRICS_TARGETS)
      ? container.get<MetricsTargets>(AGENT_HARNESS_METRICS_TARGETS)
      : DEFAULT_METRICS_TARGETS,
    logger,
    fetch,
    container.isBound(AGENT_HARNESS_LOCAL_METRICS)
      ? container.get<LocalMetrics>(AGENT_HARNESS_LOCAL_METRICS)
      : null,
  );
  router.get('/metrics', scraper, metrics.read);

  // P9: the grant auditor reads a run's pinned grant and tool intents (ids only) with the worker credential.
  router.get(
    '/runs/:runId/audit',
    worker,
    ValidationMiddleware.validate(readRunAuditSchema, { stripUnknown: true }),
    controller.auditEvidence,
  );
  if (container.isBound(AGENT_HARNESS_LIVE_LAYERS)) {
    const live = new AgentHarnessLiveLayerController(
      container.get<LiveLayerService>(AGENT_HARNESS_LIVE_LAYERS),
      logger,
    );
    // The worker credential is the deployment's, as claim-next: the org is the path's.
    router.get(
      '/control-plane/orgs/:orgId/live',
      worker,
      ValidationMiddleware.validate(readLiveLayerSchema),
      live.read,
    );
  }
  router.post(
    '/runs/claim',
    worker,
    ValidationMiddleware.validate(claimNextRunSchema, { stripUnknown: true }),
    controller.claimNext,
  );
  router.post(
    '/runs/:runId/claim',
    worker,
    ValidationMiddleware.validate(claimRunSchema, { stripUnknown: true }),
    controller.claim,
  );
  router.post(
    '/runs/:runId/renew',
    attempt,
    ValidationMiddleware.validate(renewRunSchema, { stripUnknown: true }),
    controller.renew,
  );
  router.post(
    '/runs/:runId/release',
    attempt,
    ValidationMiddleware.validate(releaseRunSchema, { stripUnknown: true }),
    controller.release,
  );
  router.post(
    '/runs/:runId/commit',
    attempt,
    ValidationMiddleware.validate(commitRunSchema, { stripUnknown: true }),
    controller.commit,
  );
  router.get(
    '/runs/:runId/events',
    attempt,
    ValidationMiddleware.validate(readRunJournalSchema),
    controller.readJournal,
  );
  const followups = new AgentHarnessFollowupsController(
    executions,
    authority,
    logger,
  );
  // UX5: after the run's terminal commit, with the attempt token that ran it.
  router.post('/runs/:runId/followups', attempt, followups.offer);

  if (container.isBound(AGENT_HARNESS_CHILD_RUNS)) {
    const children = new AgentHarnessChildController(
      container.get<ChildRunService>(AGENT_HARNESS_CHILD_RUNS),
      authority,
      logger,
    );
    router.post(
      '/runs/:runId/children',
      attempt,
      ValidationMiddleware.validate(spawnChildRunSchema),
      children.spawn,
    );
  }

  if (container.isBound(AGENT_HARNESS_BUDGET_LEDGER)) {
    const budgets = new AgentHarnessBudgetController(
      container.get<BudgetLedgerService>(AGENT_HARNESS_BUDGET_LEDGER),
      logger,
    );
    // P6.5: the run's gateway token or its attempt token; the service tells them apart.
    const run = runTokenRoute;
    router.get('/runs/:runId/budget', run(budgets.remaining));
    router.post('/runs/:runId/budget/reservations', run(budgets.reserve));
    router.post(
      '/runs/:runId/budget/reservations/:key/commit',
      run(budgets.commit),
    );
    router.post(
      '/runs/:runId/budget/reservations/:key/release',
      run(budgets.release),
    );
    router.post(
      '/runs/:runId/budget/reservations/:key/reconcile',
      run(budgets.reconcile),
    );
  }

  if (container.isBound(AGENT_HARNESS_RUN_CONTENT)) {
    const content = new AgentHarnessContentController(
      container.get<RunContentService>(AGENT_HARNESS_RUN_CONTENT),
      authority,
      logger,
    );
    router.post(
      '/runs/:runId/content',
      attempt,
      rawContentBody(logger),
      content.put,
    );
    router.get(
      '/runs/:runId/content',
      attempt,
      ValidationMiddleware.validate(readRunContentSchema),
      content.get,
    );
  }

  if (container.isBound(AGENT_HARNESS_WORKSPACES)) {
    const workspaces = new AgentHarnessWorkspaceController(
      container.get<WorkspaceService>(AGENT_HARNESS_WORKSPACES),
      authority,
      logger,
    );
    const path = '/runs/:runId/workspaces/:workspaceId';
    router.get(path, attempt, workspaces.head);
    router.get(`${path}/revisions/:revision`, attempt, workspaces.revision);
    router.post(`${path}/commit`, attempt, workspaces.commit);
    router.post(`${path}/fork`, attempt, workspaces.fork);
    router.post(`${path}/merge`, attempt, workspaces.merge);
    router.post(`${path}/rollback`, attempt, workspaces.rollback);
    router.post(`${path}/writer`, attempt, workspaces.writer);
    router.post(`${path}/writer/release`, attempt, workspaces.releaseWriter);
    router.post('/runs/:runId/workspace-key', attempt, workspaces.key);
  }

  if (container.isBound(AGENT_HARNESS_MEMORIES)) {
    const memories = new AgentHarnessMemoryController(
      container.get<MemoryService>(AGENT_HARNESS_MEMORIES),
      authority,
      logger,
    );
    // P7.3: the run's pinned memories and its model's proposals.
    router.get('/runs/:runId/memories', attempt, memories.recall);
    router.post('/runs/:runId/memories', attempt, memories.propose);
  }

  if (container.isBound(AGENT_HARNESS_CHAT_SCHEDULES)) {
    const chatSchedules = new AgentHarnessChatScheduleController(
      container.get<ChatScheduleService>(AGENT_HARNESS_CHAT_SCHEDULES),
      authority,
      logger,
    );
    // ADR-SCH-01 D3: a schedule the run's person approved in chat, made as them. Each needs a person's approval, so
    // a run asking more often than this is a broken or hostile worker.
    router.post(
      '/runs/:runId/schedules',
      attempt,
      createKeyedRateLimiter(
        container.isBound('Logger')
          ? container.get<Logger>('Logger')
          : Logger.getInstance({ service: 'AgentHarnessInternal' }),
        {
          prefix: 'harness-chat-schedules',
          maxRequestsPerMinute: CHAT_SCHEDULES_PER_RUN_PER_MINUTE,
          message: 'Too many schedule requests for this run',
          anonymousSubKey: (req) => String(req.params['runId']),
        },
      ),
      chatSchedules.create,
    );
    // ADR-SCH-03: the schedules toolset's find, the current values a card shows, and the changes the person approved.
    const changeLimit = createKeyedRateLimiter(
      container.isBound('Logger')
        ? container.get<Logger>('Logger')
        : Logger.getInstance({ service: 'AgentHarnessInternal' }),
      {
        prefix: 'harness-chat-schedule-changes',
        maxRequestsPerMinute: CHAT_SCHEDULES_PER_RUN_PER_MINUTE,
        message: 'Too many schedule requests for this run',
        anonymousSubKey: (req) => String(req.params['runId']),
      },
    );
    // Reads have their own budget, so a run that looks its schedules up does not spend what its changes may use.
    const readLimit = createKeyedRateLimiter(
      container.isBound('Logger')
        ? container.get<Logger>('Logger')
        : Logger.getInstance({ service: 'AgentHarnessInternal' }),
      {
        prefix: 'harness-chat-schedule-reads',
        maxRequestsPerMinute: CHAT_SCHEDULES_PER_RUN_PER_MINUTE,
        message: 'Too many schedule requests for this run',
        anonymousSubKey: (req) => String(req.params['runId']),
      },
    );
    router.get(
      '/runs/:runId/schedules',
      attempt,
      readLimit,
      chatSchedules.find,
    );
    router.get(
      '/runs/:runId/schedules/:scheduleId',
      attempt,
      readLimit,
      chatSchedules.read,
    );
    router.post(
      '/runs/:runId/schedules/:scheduleId/update',
      attempt,
      changeLimit,
      chatSchedules.update,
    );
    router.post(
      '/runs/:runId/schedules/:scheduleId/delete',
      attempt,
      changeLimit,
      chatSchedules.delete,
    );
  }

  if (container.isBound(AGENT_HARNESS_GATEWAY_TOKENS)) {
    const gateway = new AgentHarnessGatewayTokenController(
      container.get<GatewayTokenService>(AGENT_HARNESS_GATEWAY_TOKENS),
    );
    // The egress proxy holds its own role credential, not an attempt token (P5.4, SEC-C-10);
    // Node binds the token to the attempt only while it holds the lease.
    router.post('/runs/:runId/gateway-token', egressProxy, gateway.forAttempt);
    // E3: authenticated by the run's own gateway token (the model gateway holds no worker credential).
    router.get(
      '/runs/:runId/model-grant',
      ...runTokenRoute(gateway.modelGrant),
    );
    // SEC2 O-2: the gateways report a refusal of the run's request with the same token.
    router.post('/runs/:runId/refusals', ...runTokenRoute(gateway.refusal));
    // OB-03: the egress proxy reports a destination it let the run reach, the same way.
    router.post('/runs/:runId/egress', ...runTokenRoute(gateway.egressAllowed));
    // C6.12: an unusual egress volume of the run's sandboxes, the same way.
    router.post(
      '/runs/:runId/egress-volume',
      ...runTokenRoute(gateway.egressVolume),
    );
    // G-48 (WP-100): the model gateway reports a call it moved to the run's alternate model, the same way.
    router.post(
      '/runs/:runId/model-fallback',
      ...runTokenRoute(gateway.modelFallback),
    );
    // WP-103: a gateway reports a credential shape it hid from the run's model call, the same way.
    router.post(
      '/runs/:runId/secret-hidden',
      ...runTokenRoute(gateway.secretHidden),
    );
  }
  router.use(editionJsonBodies());
  mountEditionRoutes(
    router,
    container,
    harnessEditionOf(container).internalRoutes,
  );

  return router;
}
