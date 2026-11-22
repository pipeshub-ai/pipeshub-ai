/**
 * Connector Controllers
 *
 * Controllers for managing connector instances and configurations.
 * These controllers act as a proxy layer between the frontend and the Python backend,
 * handling authentication, validation, and error transformation.
 */

import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import { classifyError, levelFor } from '../../../libs/utils/error-class';
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  InternalServerError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  annotateLocalFsDesktopPresence,
  ConnectorInstanceSummary,
  executeConnectorCommand,
  fetchConnectorInstanceSummary,
  handleBackendError,
  handleConnectorResponse,
  localFsRefusalFromBackend,
  localFsSyncRefusal,
  respondLocalFsDesktopRefusal,
} from '../utils/connector.utils';
import { CrawlingSchedulerService } from '../../crawling_manager/services/crawling_service';
import {
  RECONCILE_LOG_MESSAGE,
  reconcileConnectorSchedule,
  ScheduleReconcileInput,
} from '../../crawling_manager/services/connector_schedule_orchestrator';
import { ConnectorSyncBlock } from '../../crawling_manager/utils/schedule_config_mapper';
import { RecordRelationService } from '../../knowledge_base/services/kb.relation.service';

const logger = Logger.getInstance({
  service: 'Connector Controller',
});

// Headers we forward to the Python connector backend. Authorization carries
// the verified caller identity (orgId/userId/role); tracing headers preserve
// request correlation. Anything else (cookie, host, user-agent, arbitrary
// x-* headers from the client) is dropped to avoid header-injection paths.
const PROXY_FORWARD_HEADERS: readonly string[] = [
  'authorization',
  'x-request-id',
  'x-correlation-id',
  'x-forwarded-for',
  'accept-language',
];

/** Allowlisted proxy headers. Admin is JWT role on Python — never X-Is-Admin. */
export const buildProxyHeaders = (
  req: AuthenticatedUserRequest,
): Record<string, string> => {
  const headers: Record<string, string> = {};
  for (const name of PROXY_FORWARD_HEADERS) {
    const value = req.headers[name];
    if (typeof value === 'string') {
      headers[name] = value;
    } else if (Array.isArray(value)) {
      headers[name] = value.join(',');
    }
  }
  return headers;
};

/**
 * Higher-order function to create connector config update handlers.
 * Reduces code duplication by centralizing common validation, header preparation,
 * API call execution, and error handling logic.
 *
 * @param appConfig - Application configuration
 * @param endpointPath - API endpoint path segment (e.g., 'auth', 'filters-sync')
 * @param validatePayload - Function to validate the request payload
 * @param createPayload - Function to create the payload from request body
 * @param operationName - Human-readable operation name for logging
 * @returns Express route handler function
 */
const createConnectorConfigUpdateHandler = (
  appConfig: AppConfig,
  endpointPath: string,
  validatePayload: (body: any) => void,
  createPayload: (body: any) => any,
  operationName: string,
  onSuccess?: (req: AuthenticatedUserRequest, body: any) => void,
) => {
  return async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      // Validate payload
      validatePayload(req.body);

      // Create payload
      const config = createPayload(req.body);

      const headers = buildProxyHeaders(req);

      // Execute API call
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/config/${endpointPath}`,
        HttpMethod.PUT,
        headers,
        config,
      );

      const isSuccess =
        connectorResponse?.statusCode != null &&
        connectorResponse.statusCode >= 200 &&
        connectorResponse.statusCode < 300;

      if (isSuccess && onSuccess) {
        try {
          onSuccess(req, req.body);
        } catch (hookError) {
          logger.error('Post-update hook threw synchronously', {
            operation: operationName,
            error: hookError,
          });
        }
      }

      // Handle response
      handleConnectorResponse(
        connectorResponse,
        res,
        operationName,
        'Connector instance not found',
      );
    } catch (error: any) {
      const handledError = handleBackendError(error, operationName.toLowerCase());
      next(handledError);
    }
  };
};

interface ConnectorSnapshot {
  type: string;
  isActive: boolean;
  ownerUserId: string;
  sync: ConnectorSyncBlock | null;
}

type SnapshotFailure = 'non_2xx' | 'no_envelope' | 'no_type' | 'request_failed';

/**
 * One failed read of the settings a reconcile needs. A read the service refused, or that timed
 * out, already has its WARN call line, so it is DEBUG here; a 2xx without the expected body, or
 * our own bug, has no other line and is ERROR. The connector comes from the request context.
 */
const logSnapshotFailure = (
  reason: SnapshotFailure,
  fields: { status?: number; error?: unknown } = {},
): void => {
  const ours =
    reason === 'no_envelope' ||
    reason === 'no_type' ||
    (reason === 'request_failed' && classifyError(fields.error) === 'internal');
  logger[ours ? 'error' : 'debug'](RECONCILE_LOG_MESSAGE, {
    outcome: 'failure',
    reason,
    ...fields,
  });
};

/**
 * Pull the post-mutation snapshot of a connector instance from the Python
 * backend so we can read `isActive`, `type`, and `config.sync` after a
 * toggle/update.
 *
 * We call only GET /connectors/:id/config (etcd config endpoint) because it
 * returns everything we need in a single response:
 *   { success, config: { type, isActive, createdBy, config: { sync, auth, filters } } }
 *
 * Crucially, the `sync` block here is read from etcd — the source of truth for
 * sync strategy. The plain GET /connectors/:id endpoint returns the ArangoDB
 * document whose `config.sync.selectedStrategy` is never updated after creation
 * (the filters-sync endpoint writes only to etcd), so it would silently return
 * the stale initial strategy (e.g. "MANUAL") even after the user changes it.
 */
/** Timeout (ms) for the background connector snapshot GET used by reconcile. */
const RECONCILE_SNAPSHOT_TIMEOUT_MS = 10_000;

const fetchConnectorSnapshot = async (
  req: AuthenticatedUserRequest,
  connectorId: string,
  appConfig: AppConfig,
): Promise<ConnectorSnapshot | null> => {
  try {
    const headers = buildProxyHeaders(req);

    const resp = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/config`,
      HttpMethod.GET,
      headers,
      undefined,
      // Aborted, not just abandoned: a hung backend must not pile up reads across the retries.
      { timeoutMs: RECONCILE_SNAPSHOT_TIMEOUT_MS },
    );
    if (!resp || resp.statusCode < 200 || resp.statusCode >= 300) {
      logSnapshotFailure('non_2xx', { status: resp.statusCode });
      return null;
    }

    // Response envelope: { success, config: <envelope> }
    // Envelope fields:   { type, isActive, createdBy, config: { sync, auth, filters } }
    const data = resp.data as Record<string, any> | null;
    const envelope = data?.config as Record<string, any> | undefined;
    if (!envelope || typeof envelope !== 'object') {
      logSnapshotFailure('no_envelope');
      return null;
    }

    const type = String(envelope.type ?? '');
    if (!type) {
      logSnapshotFailure('no_type');
      return null;
    }

    // Inner `config` key holds the etcd payload: sync / auth / filters.
    const etcdConfig = envelope.config as Record<string, any> | undefined;
    const sync = (etcdConfig?.sync ?? null) as ConnectorSyncBlock | null;

    return {
      type,
      isActive: !!envelope.isActive,
      ownerUserId: String(envelope.createdBy ?? req.user?.userId ?? ''),
      sync,
    };
  } catch (error) {
    logSnapshotFailure('request_failed', { error });
    return null;
  }
};

/** Waits (ms) before each attempt to read the connector settings for a schedule reconcile. */
const RECONCILE_RETRY_WAITS_MS = [0, 5_000, 30_000];

/** With the instance read before it, under the ~90 s a browser or proxy waits, so the user gets
 * an answer rather than a dropped connection. A timed-out toggle is not retried: the connector
 * service may still be writing it. */
export const CONNECTOR_TOGGLE_TIMEOUT_MS = 60_000;
/** The read a sync toggle makes before it; a plain lookup, so far shorter than the toggle. */
export const CONNECTOR_TOGGLE_READ_TIMEOUT_MS = 10_000;

const fireConnectorScheduleReconcile = (
  scheduler: CrawlingSchedulerService,
  req: AuthenticatedUserRequest,
  connectorId: string,
  appConfig: AppConfig,
): void => {
  const orgId = req.user?.orgId;
  const actorUserId = req.user?.userId;
  if (!orgId || !actorUserId) return;
  setImmediate(async () => {
    // A later save may mark the connector pending while this one runs; only older marks are cleared.
    const startedAt = Date.now();
    const markPending = (): Promise<void> =>
      scheduler
        .markReconcilePending(connectorId, String(orgId))
        .catch((err: unknown) => {
          logger.error(
            'Could not record a schedule change for the hourly repair',
            { error: err },
          );
        });
    // Set when the orchestrator threw: it has already written its own line for that failure.
    let reported = false;
    try {
      // A busy connector service answers late or not at all; a schedule left unwritten because of
      // that would silently never run, so the read is tried again before giving up.
      let snapshot: ConnectorSnapshot | null = null;
      for (const wait of RECONCILE_RETRY_WAITS_MS) {
        if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
        snapshot = await fetchConnectorSnapshot(req, connectorId, appConfig);
        if (snapshot) break;
      }
      if (!snapshot) {
        await markPending();
        // The reconcile's one line; the hourly repair will apply the settings.
        logger.warn(RECONCILE_LOG_MESSAGE, {
          outcome: 'failure',
          reason: 'snapshot_failed',
          attempt: RECONCILE_RETRY_WAITS_MS.length,
        });
        return;
      }

      logger.debug('Connector snapshot fetched for schedule reconcile', {
        type: snapshot.type,
        isActive: snapshot.isActive,
        selectedStrategy: snapshot.sync?.selectedStrategy,
      });

      const input: ScheduleReconcileInput = {
        connector: snapshot.type,
        connectorId,
        orgId,
        userId: snapshot.ownerUserId || actorUserId,
        isActive: snapshot.isActive,
        sync: snapshot.sync,
      };
      try {
        await reconcileConnectorSchedule(scheduler, logger, input);
      } catch (error) {
        reported = true;
        throw error;
      }
      await scheduler.clearReconcilePending(connectorId, startedAt);
    } catch (error) {
      await markPending();
      logger[reported ? 'warn' : levelFor(classifyError(error))](
        'Background schedule reconcile failed; the hourly repair will apply it',
        { error },
      );
    }
  });
};

/**
 * Org admin from req.user.role (session JWT claim, or attached at OAuth auth).
 * Session role changes invalidate prior tokens (ROLE_CHANGED).
 */
export const isUserAdmin = async (req: AuthenticatedUserRequest): Promise<boolean> => {
  const { userId, orgId, role } = req.user || {};
  if (!userId || !orgId) {
    throw new UnauthorizedError('User authentication required');
  }
  return role === 'admin';
};

// ============================================================================
// Registry & Instance Controllers
// ============================================================================

/**
 * Get all available connector types from registry.
 */
export const getConnectorRegistry =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      const { scope, page, limit, search } = req.query;

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      const queryParams = new URLSearchParams();
      if (scope) {
        queryParams.append('scope', String(scope));
      }

      if (page) {
        queryParams.append('page', String(page));
      }
      if (limit) {
        queryParams.append('limit', String(limit));
      }
      if (search) {
        queryParams.append('search', String(search));
      }

      const headers = buildProxyHeaders(req);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/registry?${queryParams.toString()}`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting all connectors from registry',
        'Connectors from registry not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(error, 'get connector registry');
      next(handledError);
    }
  };

/**
 * Get all configured connector instances.
 *
 * Query parameters (camelCase — forwarded as-is to Python via FastAPI aliases):
 *   scope            – personal | team
 *   page / limit     – pagination
 *   search           – full-text across name/type/group
 *   isAuthenticated  – true | false  (filter by auth status)
 *   isActive         – true | false  (filter by active status)
 *   connectorType    – exact type string (e.g. "Confluence")
 */
export const getConnectorInstances =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      const {
        scope,
        page,
        limit,
        search,
        isAuthenticated,
        isActive,
        connectorType,
      } = req.query;

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      if (!scope) {
        throw new BadRequestError('Scope is required');
      }

      const headers = buildProxyHeaders(req);

      const queryParams = new URLSearchParams();
      queryParams.append('scope', String(scope));
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (search) queryParams.append('search', String(search));
      if (isAuthenticated !== undefined) queryParams.append('isAuthenticated', String(isAuthenticated));
      if (isActive !== undefined) queryParams.append('isActive', String(isActive));
      if (connectorType !== undefined) queryParams.append('connectorType', String(connectorType));

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/?${queryParams.toString()}`,
        HttpMethod.GET,
        headers,
      );

      annotateLocalFsDesktopPresence(connectorResponse?.data, req.user?.orgId);
      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector instances',
        'Connector instances not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(error, 'get connector instances');
      next(handledError);
    }
  };

/**
 * Get all active connector instances.
 */
export const getActiveConnectorInstances =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/active`,
        HttpMethod.GET,
        buildProxyHeaders(req),
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting all active connectors',
        'Active connectors not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'get active connector instances',
      );
      next(handledError);
    }
  };

/**
 * Get all inactive connector instances.
 */
export const getInactiveConnectorInstances =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/inactive`,
        HttpMethod.GET,
        buildProxyHeaders(req),
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting all inactive connectors',
        'Inactive connectors not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'get inactive connector instances',
      );
      next(handledError);
    }
  };

/**
 * Get all configured connector instances.
 */
export const getConfiguredConnectorInstances =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      const { scope, page, limit, search } = req.query;

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      const headers = buildProxyHeaders(req);

      const queryParams = new URLSearchParams();
      if (scope) {
        queryParams.append('scope', String(scope));
      }
      if (page) {
        queryParams.append('page', String(page));
      }
      if (limit) {
        queryParams.append('limit', String(limit));
      }
      if (search) {
        queryParams.append('search', String(search));
      }


      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/configured?${queryParams.toString()}`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector config',
        'Connector config not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'get configured connector instances',
      );
      next(handledError);
    }
  };

// ============================================================================
// Instance Management Controllers
// ============================================================================

/**
 * Create a new connector instance.
 */
export const createConnectorInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      const { connectorType, instanceName, config, baseUrl, scope, authType } = req.body;

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      if (!connectorType || !instanceName) {
        throw new BadRequestError(
          'connector_type and instanceName are required',
        );
      }

      const headers = buildProxyHeaders(req);

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/`,
        HttpMethod.POST,
        headers,
        { connectorType, instanceName, config, baseUrl, scope, authType },
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Creating connector instance',
        'Connector config not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'create connector instance',
      );
      next(handledError);
    }
  };

/**
 * Get a specific connector instance.
 */
export const getConnectorInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      const headers = buildProxyHeaders(req);

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}`,
        HttpMethod.GET,
        headers,
      );

      annotateLocalFsDesktopPresence(connectorResponse?.data, req.user?.orgId);
      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector instance',
        'Connector schema not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(error, 'get connector instance');
      next(handledError);
    }
  };

/**
 * Get connector instance configuration.
 */
export const getConnectorInstanceConfig =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      const headers = buildProxyHeaders(req);

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/config`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector instance config',
        'Connector config and schema not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'get connector instance config',
      );
      next(handledError);
    }
  };

/**
 * Update connector instance configuration.
 */
export const updateConnectorInstanceConfig =
  (appConfig: AppConfig, scheduler: CrawlingSchedulerService) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      const { auth, sync, filters, baseUrl } = req.body;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      const config = {
        auth,
        sync,
        filters,
        baseUrl: baseUrl,
      };

      const headers = buildProxyHeaders(req);

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/config`,
        HttpMethod.PUT,
        headers,
        config,
      );

      const isSuccess =
        connectorResponse?.statusCode != null &&
        connectorResponse.statusCode >= 200 &&
        connectorResponse.statusCode < 300;

      // Sync block changes warrant a reconcile; we fetch a fresh snapshot
      // (Python may merge / mutate sync server-side) before scheduling.
      if (isSuccess && sync !== undefined) {
        fireConnectorScheduleReconcile(scheduler, req, connectorId, appConfig);
      }

      handleConnectorResponse(
        connectorResponse,
        res,
        'Updating connector instance config',
        'Connector instance not found',
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'update connector instance config',
      );
      next(handledError);
    }
  };

/**
 * Update authentication configuration for a connector instance.
 * Clears credentials and OAuth state, marks connector as not authenticated.
 */
export const updateConnectorInstanceAuthConfig = (appConfig: AppConfig) =>
  createConnectorConfigUpdateHandler(
    appConfig,
    'auth',
    (body) => {
      if (!body.auth) {
        throw new BadRequestError('Auth configuration is required');
      }
    },
    (body) => ({
      auth: body.auth,
      baseUrl: body.baseUrl,
    }),
    'Updating connector instance auth config',
  );

/**
 * Update filters and sync configuration for a connector instance.
 * Validates that connector is not active and authentication is valid.
 */
export const updateConnectorInstanceFiltersSyncConfig = (
  appConfig: AppConfig,
  scheduler: CrawlingSchedulerService,
) =>
  createConnectorConfigUpdateHandler(
    appConfig,
    'filters-sync',
    (body) => {
      if (!body.sync && !body.filters) {
        throw new BadRequestError('Sync or filters configuration is required');
      }
    },
    (body) => ({
      sync: body.sync,
      filters: body.filters,
      baseUrl: body.baseUrl,
    }),
    'Updating connector instance filters-sync config',
    (req, body) => {
      if (body?.sync === undefined) return;
      const { connectorId } = req.params;
      if (!connectorId) return;
      fireConnectorScheduleReconcile(scheduler, req, connectorId, appConfig);
    },
  );

/**
 * Delete a connector instance. On success its sync schedule is removed in the
 * background, found by connector id, so it does not outlive the connector.
 */
export const deleteConnectorInstance =
  (appConfig: AppConfig, scheduler: CrawlingSchedulerService) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      const headers = buildProxyHeaders(req);

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}`,
        HttpMethod.DELETE,
        headers,
      );

      const isSuccess =
        connectorResponse?.statusCode != null &&
        connectorResponse.statusCode >= 200 &&
        connectorResponse.statusCode < 300;

      const orgId = req.user?.orgId;
      if (isSuccess && orgId) {
        setImmediate(async () => {
          try {
            await scheduler.removeJobsForConnector(connectorId, orgId);
            logger.debug('Removed sync schedule after connector deletion');
          } catch (err) {
            logger.error('Failed to remove sync schedule after connector deletion', {
              error: err,
            });
          }
        });
      }

      handleConnectorResponse(
        connectorResponse,
        res,
        'Deleting connector instance',
        'Connector instance not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'delete connector instance',
      );
      next(handledError);
    }
  };

/**
 * Update connector instance name.
 */
export const updateConnectorInstanceName =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      const { instanceName } = req.body as { instanceName: string };

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }
      if (!instanceName || !instanceName.trim()) {
        throw new BadRequestError('instanceName is required');
      }

      const headers = buildProxyHeaders(req);

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/name`,
        HttpMethod.PUT,
        headers,
        { instanceName: instanceName },
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Updating connector instance name',
        'Connector instance not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'update connector instance name',
      );
      next(handledError);
    }
  };

// ============================================================================
// OAuth Controllers
// ============================================================================

/**
 * Get OAuth authorization URL for a connector instance.
 */
export const getOAuthAuthorizationUrl =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      const { baseUrl, desktop } = req.query;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      const queryParams = new URLSearchParams();
      if (baseUrl) {
        queryParams.set('base_url', String(baseUrl));
      }
      if (desktop === 'true') {
        queryParams.set('desktop', 'true');
      }

      const authorizationUrl = `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/oauth/authorize?${queryParams.toString()}`;

      const headers = buildProxyHeaders(req);

      const connectorResponse = await executeConnectorCommand(
        authorizationUrl,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting OAuth authorization URL',
        'OAuth authorization URL not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'get OAuth authorization URL',
      );
      next(handledError);
    }
  };

/**
 * Handle OAuth callback.
 */
export const handleOAuthCallback =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { baseUrl, code, state, error } = req.query;

      if (!code || !state) {
        throw new BadRequestError('Code and state are required');
      }

      const queryParams = new URLSearchParams();
      if (code) queryParams.set('code', String(code));
      if (state) queryParams.set('state', String(state));
      if (error) queryParams.set('error', String(error));
      if (baseUrl) queryParams.set('base_url', String(baseUrl));

      const callbackUrl = `${appConfig.connectorBackend}/api/v1/connectors/oauth/callback?${queryParams.toString()}`;

      const headers = buildProxyHeaders(req);

      const connectorResponse = await executeConnectorCommand(
        callbackUrl,
        HttpMethod.GET,
        headers,
      );

      // Handle redirect responses
      if (
        connectorResponse &&
        connectorResponse.statusCode === 302 &&
        connectorResponse.headers?.location
      ) {
        const redirectUrl = connectorResponse.headers.location;
        res.status(200).json({ redirectUrl });
        return;
      }

      // Handle JSON responses with redirect URL
      if (connectorResponse && connectorResponse.data) {
        const responseData = connectorResponse.data as any;
        const redirectUrlFromJson = responseData.redirect_url as
          | string
          | undefined;

        if (redirectUrlFromJson) {
          const result: Record<string, unknown> = { redirectUrl: redirectUrlFromJson };
          if (responseData.success === true) {
            result.success = true;
          } else if (responseData.success === false) {
            result.success = false;
            if (responseData.error) {
              result.error = responseData.error;
            }
            if (responseData.error_message) {
              result.errorMessage = responseData.error_message;
            }
          }
          res.status(200).json(result);
          return;
        }
      }

      // Handle normal response
      handleConnectorResponse(
        connectorResponse,
        res,
        'Handling OAuth callback',
        'OAuth callback failed'
      );
    } catch (error: any) {
      const handledError = handleBackendError(error, 'handle OAuth callback');
      next(handledError);
    }
  };

// ============================================================================
// Filter Controllers
// ============================================================================

/**
 * Get filter options for a connector instance.
 */
export const getConnectorInstanceFilterOptions =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      const headers = buildProxyHeaders(req);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/filters`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector instance filter options',
        'Connector instance filter options not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'get connector instance filter options',
      );
      next(handledError);
    }
  };

/**
 * Get dynamic filter field options for a connector instance.
 */
export const getFilterFieldOptions =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId, filterKey } = req.params;
      const { page, limit, search, cursor, contextGroupPath, excludeContextGroupPath } = req.query;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      if (!filterKey) {
        throw new BadRequestError('Filter key is required');
      }

      const headers = buildProxyHeaders(req);

      // Build query string with cursor support
      const queryParams = new URLSearchParams();
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (search) queryParams.append('search', String(search));
      if (cursor) queryParams.append('cursor', String(cursor));
      if (contextGroupPath && Array.isArray(contextGroupPath)) {
        for (const p of contextGroupPath) {
          if (p && String(p).trim()) queryParams.append('contextGroupPath', String(p).trim());
        }
      } else if (typeof contextGroupPath === 'string' && contextGroupPath.trim()) {
        queryParams.append('contextGroupPath', contextGroupPath.trim());
      }
      if (excludeContextGroupPath && Array.isArray(excludeContextGroupPath)) {
        for (const p of excludeContextGroupPath) {
          if (p && String(p).trim())
            queryParams.append('excludeContextGroupPath', String(p).trim());
        }
      } else if (
        typeof excludeContextGroupPath === 'string' &&
        excludeContextGroupPath.trim()
      ) {
        queryParams.append('excludeContextGroupPath', excludeContextGroupPath.trim());
      }
      const queryString = queryParams.toString() ? `?${queryParams.toString()}` : '';

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/filters/${encodeURIComponent(filterKey)}/options${queryString}`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting filter field options',
        'Filter field options not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'get filter field options',
      );
      next(handledError);
    }
  };

/**
 * Save filter options for a connector instance.
 */
export const saveConnectorInstanceFilterOptions =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      const { filters } = req.body;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      if (!filters) {
        throw new BadRequestError('Filters are required');
      }

      logger.warn(
        'Deprecated POST /api/v1/connectors/:connectorId/filters was called; use PUT /api/v1/connectors/:connectorId/config/filters-sync with {sync?, filters?: {sync, indexing}} while the connector is disabled. This endpoint will be removed in a future release.',
      );

      const headers = buildProxyHeaders(req);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/filters`,
        HttpMethod.POST,
        headers,
        { filters },
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Saving connector instance filter options',
        'Connector instance filter options not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'save connector instance filter options',
      );
      next(handledError);
    }
  };

// ============================================================================
// Toggle Controller
// ============================================================================

/**
 * Toggle connector instance active status.
 */
export const toggleConnectorInstance =
  (appConfig: AppConfig, scheduler: CrawlingSchedulerService) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      const { type, enabled, fullSync, deviceId, deviceName } = req.body;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      if (!type) {
        throw new BadRequestError('Toggle type is required');
      }

      const headers = buildProxyHeaders(req);
      let ownerDeviceName: string | null | undefined;
      // Enabling sync publishes an immediate pull, so refuse up front when the
      // owner device cannot serve it. Agent toggles and toggle-off skip the
      // extra round-trip; Python re-checks the claim when it writes the owner.
      if (type === 'sync') {
        const instance = await fetchConnectorInstanceSummary(
          connectorId,
          appConfig,
          headers,
          { timeoutMs: CONNECTOR_TOGGLE_READ_TIMEOUT_MS },
        );
        ownerDeviceName = instance.ownerDeviceName;
        // An off connector asked to stay off is left alone by the service, so
        // only a real enable needs a device that can serve the pull.
        if (instance.isActive === false && enabled !== false) {
          const refusal = localFsSyncRefusal(req.user?.orgId, instance, {
            fallbackUserId: req.user?.userId,
            requestDeviceId: deviceId,
          });
          if (refusal) {
            respondLocalFsDesktopRefusal(
              res,
              connectorId,
              refusal,
              instance.ownerDeviceName,
            );
            return;
          }
        }
      }
      const body: {
        type: string;
        enabled?: boolean;
        fullSync?: boolean;
        deviceId?: string;
        deviceName?: string;
      } = { type };
      if (typeof enabled === 'boolean') body.enabled = enabled;
      if (typeof fullSync === 'boolean') {
        body.fullSync = fullSync;
      }
      if (typeof deviceId === 'string') body.deviceId = deviceId;
      if (typeof deviceName === 'string') body.deviceName = deviceName;
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/toggle`,
        HttpMethod.POST,
        headers,
        body,
        {
          timeoutMs: CONNECTOR_TOGGLE_TIMEOUT_MS,
          // A request naming the state it wants can be sent again after a
          // dropped connection; a flip sent twice turns the connector back.
          retries: typeof enabled === 'boolean' ? 3 : 1,
        },
      );

      const isSuccess =
        connectorResponse?.statusCode != null &&
        connectorResponse.statusCode >= 200 &&
        connectorResponse.statusCode < 300;

      // Python's claim check is the backstop for a race with another device;
      // keep its code in the refusal shape the client reads.
      const backendRefusal = localFsRefusalFromBackend(connectorResponse);
      if (backendRefusal) {
        respondLocalFsDesktopRefusal(
          res,
          connectorId,
          backendRefusal,
          ownerDeviceName,
        );
        return;
      }

      // Only the `sync` toggle affects crawling; agent toggles are a
      // separate concern and must not touch BullMQ jobs.
      if (isSuccess && type === 'sync') {
        fireConnectorScheduleReconcile(scheduler, req, connectorId, appConfig);
      }

      handleConnectorResponse(
        connectorResponse,
        res,
        'Toggling connector instance',
        'Connector instance not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(
        error,
        'toggle connector instance',
      );
      next(handledError);
    }
  };

// ============================================================================
// Schema Controller
// ============================================================================

/**
 * Get connector schema from registry.
 */
export const getConnectorSchema =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorType } = req.params;

      if (!connectorType) {
        throw new BadRequestError('Connector type is required');
      }

      const headers = buildProxyHeaders(req);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/registry/${encodeURIComponent(connectorType)}/schema`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector schema',
        'Connector schema not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(error, 'get connector schema');
      next(handledError);
    }
  };

/**
 * Try auth settings from the setup form before they are saved.
 * A failed check comes back as 200 with `success: false` and a message for the user.
 */
export const testConnectorConnection =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorType } = req.params;
      // The route's schema has checked this shape.
      const { auth, authType, connectorId } = req.body as {
        auth: Record<string, unknown>;
        authType?: string;
        connectorId?: string;
      };

      if (!connectorType) {
        throw new BadRequestError('Connector type is required');
      }

      const headers = buildProxyHeaders(req);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/registry/${encodeURIComponent(connectorType)}/test-connection`,
        HttpMethod.POST,
        headers,
        { auth, connectorId, ...(authType ? { authType } : {}) },
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Testing connector connection',
        'Connector type not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(error, 'test the connection');
      next(handledError);
    }
  };

/**
 * Public IPs connectors connect from, for users to allow in their firewalls.
 */
export const getConnectorEgressIps =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const headers = buildProxyHeaders(req);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/network/egress-ips`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector egress IPs',
        'Egress IPs not found'
      );
    } catch (error: any) {
      const handledError = handleBackendError(error, 'get the connector IP addresses');
      next(handledError);
    }
  };


  /**
 * Get all active agent instances.
 */
export const getActiveAgentInstances =
(appConfig: AppConfig) =>
async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const { userId } = req.user || {};
    const { scope, page, limit, search } = req.query;

    if (!userId) {
      throw new UnauthorizedError('User authentication required');
    }

    const queryParams = new URLSearchParams();
    if (scope) {
      queryParams.append('scope', String(scope));
    }

    if (page) {
      queryParams.append('page', String(page));
    }
    if (limit) {
      queryParams.append('limit', String(limit));
    }
    if (search) {
      queryParams.append('search', String(search));
    }

    const headers = buildProxyHeaders(req);
    const connectorResponse = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/connectors/agents/active?${queryParams.toString()}`,
      HttpMethod.GET,
      headers,
    );

    handleConnectorResponse(
      connectorResponse,
      res,
      'Getting active agent instances',
      'Failed to get active agent instances',
    );
  } catch (error: any) {
    const handledError = handleBackendError(
      error,
      'get active agent instances',
    );
    next(handledError);
  }
};

/** Admin only, and decided by the connector service, which answers anyone else with the 404 of a missing connector. */
export const getPermissionStatus =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(req.params.connectorId as string)}/permissions/status`,
        HttpMethod.GET,
        buildProxyHeaders(req),
      );
      handleConnectorResponse(
        response,
        res,
        'Getting permission status',
        'Connector not found',
      );
    } catch (error) {
      next(handleBackendError(error, 'get permission status'));
    }
  };

/** Admin only, decided by the connector service as for the status: it writes the connector's own permission filter mode and answers with the status. */
export const setPermissionFilter =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { mode } = req.body as { mode: string };
      const response = await executeConnectorCommand<{
        detail?: { error?: string };
      }>(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(req.params.connectorId as string)}/permissions/filter`,
        HttpMethod.PATCH,
        buildProxyHeaders(req),
        { mode },
      );
      // The refusal carries the blockers the card lists; the generic mapper would flatten them to a message.
      if (
        response.statusCode === 409 &&
        response.data?.detail?.error === 'not_switchable'
      ) {
        res.status(409).json(response.data.detail);
        return;
      }
      handleConnectorResponse(
        response,
        res,
        'Setting the permission filter',
        'Connector not found',
      );
    } catch (error) {
      next(handleBackendError(error, 'set permission filter'));
    }
  };

/**
 * Per-user access: a user's own source account on a team connector. Proxied as is; the
 * connector service decides who may do what and never returns a credential.
 */
type UserAccessHandler = (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
) => Promise<void>;

const proxyUserAccess =
  (
    appConfig: AppConfig,
    method: HttpMethod,
    operation: string,
    suffix: (req: AuthenticatedUserRequest) => string = () => '',
    // The route's validated body; only POST routes send one.
    body: (req: AuthenticatedUserRequest) => unknown = () => undefined,
  ): UserAccessHandler =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(req.params.connectorId as string)}/user-access${suffix(req)}`,
        method,
        buildProxyHeaders(req),
        body(req),
        // A retried POST would send invites twice; the connector service may have done the work.
        { retries: method === HttpMethod.POST ? 1 : 3 },
      );
      handleConnectorResponse(response, res, operation, 'Connector not found');
    } catch (error) {
      next(handleBackendError(error, operation));
    }
  };

/** The organisation's per-user connectors, each with whether the caller has connected. */
export const listMyPerUserConnectors =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/user-access/mine`,
        HttpMethod.GET,
        buildProxyHeaders(req),
      );
      handleConnectorResponse(
        response,
        res,
        'list your connectable accounts',
        'No data',
      );
    } catch (error) {
      next(handleBackendError(error, 'list your connectable accounts'));
    }
  };

export const getMyConnectorConnection = (
  appConfig: AppConfig,
): UserAccessHandler =>
  proxyUserAccess(appConfig, HttpMethod.GET, 'get your connection');

export const connectMyConnectorAccount = (
  appConfig: AppConfig,
): UserAccessHandler =>
  proxyUserAccess(
    appConfig,
    HttpMethod.POST,
    'connect your account',
    undefined,
    (req) => ({ token: (req.body as { token: string }).token }),
  );

/** The URL that signs the caller in with the source through the connector's OAuth app. */
export const startMyConnectorSignIn = (
  appConfig: AppConfig,
): UserAccessHandler =>
  proxyUserAccess(
    appConfig,
    HttpMethod.GET,
    'start your sign-in',
    () => '/oauth/authorize',
  );

export const disconnectMyConnectorAccount = (
  appConfig: AppConfig,
): UserAccessHandler =>
  proxyUserAccess(appConfig, HttpMethod.DELETE, 'disconnect your account');

export const listConnectorUserConnections = (
  appConfig: AppConfig,
): UserAccessHandler =>
  proxyUserAccess(
    appConfig,
    HttpMethod.GET,
    'list connected users',
    () => '/users',
  );

export const removeConnectorUserConnection = (
  appConfig: AppConfig,
): UserAccessHandler =>
  proxyUserAccess(
    appConfig,
    HttpMethod.DELETE,
    'remove a connected user',
    (req) => `/users/${encodeURIComponent(req.params.userId as string)}`,
  );

/** Admin only: everyone who could connect, with their connection and invite state. */
export const getConnectorUserRoster = (
  appConfig: AppConfig,
): UserAccessHandler =>
  proxyUserAccess(
    appConfig,
    HttpMethod.GET,
    'list the connector roster',
    (req) => {
      const query = new URLSearchParams();
      for (const key of ['q', 'offset', 'limit'] as const) {
        const value: unknown = req.query[key];
        if (typeof value === 'string' || typeof value === 'number') {
          query.set(key, String(value));
        }
      }
      const qs = query.toString();
      return qs !== '' ? `/roster?${qs}` : '/roster';
    },
  );

/** Admin only: notify people (by id, or everyone not yet connected) to connect their account. */
export const inviteConnectorUsers = (appConfig: AppConfig): UserAccessHandler =>
  proxyUserAccess(
    appConfig,
    HttpMethod.POST,
    'invite users to connect',
    () => '/invites',
    (req) => req.body,
  );

/** The caller turns reminders to connect this connector off or on. */
export const setMyConnectorReminders = (
  appConfig: AppConfig,
): UserAccessHandler =>
  proxyUserAccess(
    appConfig,
    HttpMethod.POST,
    'update your reminders',
    () => '/reminders',
    (req) => ({ optedOut: (req.body as { optedOut: boolean }).optedOut }),
  );

/**
 * Public: the routes behind an emailed connect link. The link token is the only credential,
 * so no caller identity is forwarded even if the browser sent one.
 */
export const proxyUserAccessLink =
  (appConfig: AppConfig, name: 'info' | 'authorize' | 'complete' | 'opt-out') =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const operation = `connect link ${name}`;
    try {
      const headers = buildProxyHeaders(req);
      delete headers.authorization;
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/user-access/link/${name}`,
        HttpMethod.POST,
        headers,
        req.body,
        // An OAuth code works once: a retry after the service used it would report a false failure.
        { retries: name === 'complete' ? 1 : 3 },
      );
      handleConnectorResponse(response, res, operation, 'No data');
    } catch (error) {
      next(handleBackendError(error, operation));
    }
  };

export const getConnectorStats =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { userId, orgId } = req.user || {};

      // Validate user authentication
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      if (!req.params.connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      const queryParams = new URLSearchParams();
      queryParams.append('connector_id', req.params.connectorId);

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/stats?${queryParams.toString()}`,
        HttpMethod.GET,
        buildProxyHeaders(req),
      );

      handleConnectorResponse(
        response,
        res,
        'Getting connector stats',
        'Connector stats not found',
      );
    } catch (error: any) {
      next(handleBackendError(error, 'get connector stats'));
      return;
    }
  };

export const getConnectorProgress =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId, orgId } = req.user || {};
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }
      if (!req.params.connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      const queryParams = new URLSearchParams();
      queryParams.append('connector_id', req.params.connectorId);
      queryParams.append(
        'detail',
        req.query.detail === 'full' ? 'full' : 'summary',
      );

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/progress?${queryParams.toString()}`,
        HttpMethod.GET,
        buildProxyHeaders(req),
      );

      handleConnectorResponse(
        response,
        res,
        'Getting connector progress',
        'Connector not found',
      );
    } catch (error: unknown) {
      logger.error('Error getting connector progress', {
        connectorId: req.params.connectorId,
        error,
      });
      next(handleBackendError(error, 'get connector progress'));
      return;
    }
  };

/**
 * The query service parameters for a budgeted read, or null when the caller
 * asked for none and gets the whole record from the connector service.
 */
export const budgetedReadParams = (
  query: Record<string, unknown> | undefined,
): URLSearchParams | null => {
  const names: Array<[string, string]> = [
    ['mode', 'mode'],
    ['query', 'query'],
    ['startBlock', 'start_block'],
    ['startRecord', 'start_record'],
    ['maxChars', 'max_chars'],
    ['extract', 'extract'],
  ];
  const params = new URLSearchParams();
  for (const [from, to] of names) {
    const value = query?.[from];
    if (
      (typeof value === 'string' && value !== '') ||
      typeof value === 'number'
    ) {
      params.append(to, String(value));
    }
  }
  return params.toString() !== '' ? params : null;
};

export const getRecordContent =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { recordId } = req.params as { recordId: string };
      const { userId, orgId } = req.user || {};

      // Validate user authentication
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      // Part of a record, or a Slack thread, is read by the query service:
      // ranking a record's blocks against a question needs the search index,
      // which lives there. With no such parameter the whole record comes from
      // the connector service, exactly as before.
      const budgeted = budgetedReadParams(req.query as Record<string, unknown>);
      const uri = budgeted
        ? `${appConfig.aiBackend}/api/v1/records/${encodeURIComponent(recordId)}/read?${budgeted.toString()}`
        : `${appConfig.connectorBackend}/api/v1/records/${encodeURIComponent(recordId)}/content`;

      // Forward only the proxy allowlist (Authorization + tracing). Never raw req.headers.
      const response = await executeConnectorCommand(
        uri,
        HttpMethod.GET,
        buildProxyHeaders(req),
      );

      handleConnectorResponse(
        response,
        res,
        'Getting record content',
        'Record content not found',
      );
    } catch (error: any) {
      const handleError = handleBackendError(error, 'get record content');
      next(handleError);
      return;
    }
  };

export const navigateKnowledgeGraph =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { userId, orgId } = req.user || {};

      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      const {
        nodeId,
        page,
        limit,
        depth,
        nodeTypes,
        createdAfter,
        createdBefore,
        modifiedAfter,
        modifiedBefore,
        cursor,
      } = req.query;

      const queryParams = new URLSearchParams();
      if (nodeId) queryParams.append('node_id', String(nodeId));
      if (typeof cursor === 'string' && cursor !== '')
        queryParams.append('cursor', cursor);
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (depth) queryParams.append('depth', String(depth));
      // Append per item: String(['a','b']) joins with a comma, which FastAPI
      // would read as one value literally named `a,b`.
      if (Array.isArray(nodeTypes)) {
        for (const t of nodeTypes) {
          if (t && String(t).trim()) queryParams.append('node_types', String(t).trim());
        }
      }
      if (createdAfter) queryParams.append('created_after', String(createdAfter));
      if (createdBefore) queryParams.append('created_before', String(createdBefore));
      if (modifiedAfter) queryParams.append('modified_after', String(modifiedAfter));
      if (modifiedBefore) queryParams.append('modified_before', String(modifiedBefore));

      const queryString = queryParams.toString() ? `?${queryParams.toString()}` : '';

      // Forward only the proxy allowlist (Authorization + tracing). Never raw req.headers.
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/knowledge-graph/navigate${queryString}`,
        HttpMethod.GET,
        buildProxyHeaders(req),
      );

      handleConnectorResponse(
        response,
        res,
        'Navigating knowledge graph',
        'Navigation result not found',
      );
    } catch (error: any) {
      const handleError = handleBackendError(error, 'navigate knowledge graph');
      next(handleError);
      return;
    }
  };

export const lookupRecord =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { userId, orgId } = req.user || {};

      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      const { identifiers, connectorName } = req.query;

      // Append per item: String(['a','b']) joins with a comma, which corrupts
      // any identifier that is a URL.
      const queryParams = new URLSearchParams();
      if (Array.isArray(identifiers)) {
        for (const id of identifiers) {
          if (id && String(id).trim()) queryParams.append('identifiers', String(id).trim());
        }
      }
      if (connectorName) queryParams.append('connector_name', String(connectorName));

      if (!queryParams.has('identifiers')) {
        throw new BadRequestError('At least one identifier is required');
      }

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/knowledge-graph/lookup?${queryParams.toString()}`,
        HttpMethod.GET,
        buildProxyHeaders(req),
      );

      handleConnectorResponse(
        response,
        res,
        'Looking up records',
        'Lookup result not found',
      );
    } catch (error: any) {
      const handleError = handleBackendError(error, 'look up records');
      next(handleError);
      return;
    }
  };

interface ConnectorInfo {
  _key: string;
}

interface ActiveConnectorsResponse {
  connectors: ConnectorInfo[];
}

const validateActiveConnector = async (
  connectorId: string,
  appConfig: AppConfig,
  headers: Record<string, string>,
): Promise<void> => {
  const activeAppsResponse = await executeConnectorCommand(
    `${appConfig.connectorBackend}/api/v1/connectors/active`,
    HttpMethod.GET,
    headers,
  );

  if (activeAppsResponse.statusCode !== 200) {
    throw new InternalServerError('Failed to get active connectors');
  }

  const data = activeAppsResponse.data as ActiveConnectorsResponse;
  const connectors = data?.connectors || [];

  const isAllowed = connectors.some(
    (connector) => connector._key === connectorId,
  );

  if (!isAllowed) {
    throw new BadRequestError(`Connector ${connectorId} not allowed`);
  }

  logger.debug('Connector validation successful', {
    connectorId,
  });
};

const LOCK_MESSAGES: Record<string, string> = {
  FULL_SYNCING: 'A full sync is in progress. Please wait and try again.',
  SYNCING: 'A sync is already in progress. Please wait and try again.',
  QUEUED: 'A sync is already queued for this connector and will start shortly.',
  DELETING: 'This connector is being deleted.',
};

const assertConnectorNotLocked = (
  instance: ConnectorInstanceSummary | null,
): void => {
  // Fast UX feedback only — Python's admission check is the hard guarantee that
  // a new sync never starts while one is running. isLocked alone is not enough:
  // it is set only during the brief full-sync prep window, so a duplicate resync
  // during a normal sync used to sail past.
  const status = (instance?.status ?? '').toUpperCase();
  if (
    !instance?.isLocked &&
    status !== 'SYNCING' &&
    status !== 'FULL_SYNCING' &&
    status !== 'QUEUED' &&
    status !== 'DELETING'
  ) {
    return;
  }
  const message =
    LOCK_MESSAGES[status] ??
    'Another operation is in progress. Please wait and try again.';
  throw new ConflictError(message);
};

const normalizeAppName = (value: string): string =>
  // Global, matching Python's str.replace. A string pattern replaces only the
  // first space; downstream normalization masked that for routing, but it left
  // an embedded space in the value carried on the event payload.
  value.replace(/ /g, '').toLowerCase();

const proxyVectorStoreJob =
  (appConfig: AppConfig, operation: 'cleanup' | 'reindex') =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { userId, orgId } = req.user || {};
      if (!userId || !orgId) {
        throw new UnauthorizedError('User not authenticated or missing organization ID');
      }

      const headers = buildProxyHeaders(req);
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/vector-store/${operation}`,
        HttpMethod.POST,
        headers,
        {},
      );

      handleConnectorResponse(
        response,
        res,
        `Vector store ${operation} not started`,
        `Vector store ${operation} failed`,
      );
    } catch (error: any) {
      next(handleBackendError(error, `vector store ${operation}`));
    }
  };

export const cleanupVectorStore = (appConfig: AppConfig) =>
  proxyVectorStoreJob(appConfig, 'cleanup');

export const reindexVectorStore = (appConfig: AppConfig) =>
  proxyVectorStoreJob(appConfig, 'reindex');

export const reindexConnector =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { connectorId } = req.params as { connectorId: string };
      const { userId, orgId } = req.user || {};
      const { statusFilters, stages } = (req.body ?? {}) as {
        statusFilters?: string[];
        stages?: string[];
      };

      if (!userId || !orgId) {
        throw new UnauthorizedError('User not authenticated or missing organization ID');
      }

      const reindexBody: { statusFilters?: string[]; stages?: string[] } = {};
      if (statusFilters?.length) {
        reindexBody.statusFilters = statusFilters;
      }
      if (stages?.length) {
        reindexBody.stages = stages;
      }

      const headers = buildProxyHeaders(req);

      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/reindex`,
        HttpMethod.POST,
        headers,
        reindexBody,
      );

      handleConnectorResponse(response, res, 'Connector not found', 'Connector not reindexed');
    } catch (error: any) {
      next(handleBackendError(error, 'reindex connector'));
      return;
    }
  };

export const resyncConnectorRecords =
  (recordRelationService: RecordRelationService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const userId = req.user?.userId;
      const orgId = req.user?.orgId;
      const connectorName = req.body.connectorName;
      const fullSync = req.body.fullSync || false;
      if (!userId || !orgId) {
        throw new BadRequestError('User not authenticated');
      }

      const connectorId = req.params.connectorId;
      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      const headers = buildProxyHeaders(req);

      await validateActiveConnector(
        connectorId,
        appConfig,
        headers,
      );

      const instance = await fetchConnectorInstanceSummary(
        connectorId,
        appConfig,
        headers,
      );
      // The read above can be wider than who may sync: a personal connector is
      // its creator's to sync.
      if (instance.scope === 'personal' && instance.createdBy !== userId) {
        throw new ForbiddenError(
          'Only the person who created this connector can sync it',
        );
      }
      // Lock first: "already running" must win over "desktop offline".
      assertConnectorNotLocked(instance);
      const refusal = localFsSyncRefusal(orgId, instance, {
        fallbackUserId: userId,
      });
      if (refusal) {
        respondLocalFsDesktopRefusal(
          res,
          connectorId,
          refusal,
          instance.ownerDeviceName,
        );
        return;
      }

      const resyncConnectorPayload = {
        userId,
        orgId,
        connectorName: normalizeAppName(connectorName),
        connectorId,
        fullSync,
      };

      const resyncConnectorResponse =
        await recordRelationService.resyncConnectorRecords(
          resyncConnectorPayload,
        );

      res.status(200).json({
        resyncConnectorResponse,
      });

      return; // Added return statement
    } catch (error: any) {
      next(handleBackendError(error, 'resync connector'));
      return; // Added return statement
    }
  };
  
export const stopConnectorSync =
  (appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { connectorId } = req.params as { connectorId: string };
      const { userId, orgId } = req.user || {};

      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }

      const headers = buildProxyHeaders(req);

      // Deliberately not guarded by assertConnectorNotLocked — stop has to work
      // precisely when the connector *is* busy, and also against a lock left
      // stuck by a crash.
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/sync/stop`,
        HttpMethod.POST,
        headers,
      );

      handleConnectorResponse(
        response,
        res,
        'stopping connector sync',
        'Failed to stop sync',
      );
    } catch (error: any) {
      next(handleBackendError(error, 'stop connector sync'));
      return;
    }
  };

/**
 * Why a person can or cannot read a record, for an organisation admin (the route
 * checks the role). The audit line names who asked, about whom and which record,
 * with the reason code the answer gave: ids only, never record content.
 */
export const explainPermission =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const user = req.user as { userId?: string; orgId?: string } | undefined;
    const { userId, orgId } = user ?? {};
    const connectorId = req.params.connectorId as string;
    // Who asked, the org and the connector come from the request context.
    // The route's schema has already required both as strings.
    const { userId: targetUserId, recordId } = req.query as {
      userId: string;
      recordId: string;
    };
    let answer: { status?: number; code?: string; admitted?: boolean } = {};
    let outcome: 'success' | 'failure' = 'success';
    try {
      if (!userId || !orgId) {
        throw new UnauthorizedError(
          'User not authenticated or missing organization ID',
        );
      }
      const queryParams = new URLSearchParams({
        user_id: targetUserId,
        record_id: recordId,
      });
      const response = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/permissions/explain?${queryParams.toString()}`,
        HttpMethod.GET,
        buildProxyHeaders(req),
      );
      const data = response.data as
        { code?: string; admitted?: boolean } | undefined;
      answer = {
        status: response.statusCode,
        code: data?.code,
        admitted: data?.admitted,
      };
      handleConnectorResponse(
        response,
        res,
        'Explaining access',
        'Explanation not found',
      );
    } catch (error: unknown) {
      outcome = 'failure';
      next(handleBackendError(error, 'explain access'));
    } finally {
      logger.info('Access explanation requested', {
        target_user_id: targetUserId,
        record_id: recordId,
        outcome,
        ...answer,
      });
    }
  };
