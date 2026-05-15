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
import {
  BadRequestError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { UserGroups } from '../../user_management/schema/userGroup.schema';
import { executeConnectorCommand, handleBackendError, handleConnectorResponse } from '../utils/connector.utils';
import { CrawlingSchedulerService } from '../../crawling_manager/services/crawling_service';
import {
  reconcileConnectorSchedule,
  ScheduleReconcileInput,
} from '../../crawling_manager/services/connector_schedule_orchestrator';
import { ConnectorSyncBlock } from '../../crawling_manager/utils/schedule_config_mapper';

const logger = Logger.getInstance({
  service: 'Connector Controller',
});

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

      logger.info(`${operationName} for ${connectorId}`);

      // Prepare headers with admin flag
      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      // Execute API call
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/config/${endpointPath}`,
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
            connectorId,
            operationName,
            error:
              hookError instanceof Error ? hookError.message : 'Unknown error',
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
      logger.error(`Error ${operationName.toLowerCase()}`, {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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
const fetchConnectorSnapshot = async (
  req: AuthenticatedUserRequest,
  connectorId: string,
  appConfig: AppConfig,
): Promise<ConnectorSnapshot | null> => {
  try {
    const isAdmin = await isUserAdmin(req);
    const headers: Record<string, string> = {
      ...(req.headers as Record<string, string>),
      'X-Is-Admin': isAdmin ? 'true' : 'false',
    };

    const resp = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/config`,
      HttpMethod.GET,
      headers,
    );
    if (!resp || resp.statusCode < 200 || resp.statusCode >= 300) return null;

    const data = resp.data as Record<string, any> | null;
    if (!data) return null;

    // Response envelope: { success, config: <envelope> }
    // Envelope fields:   { type, isActive, createdBy, config: { sync, auth, filters } }
    const envelope = data.config as Record<string, any> | undefined;
    if (!envelope || typeof envelope !== 'object') return null;

    const type = String(envelope.type ?? '');
    if (!type) {
      logger.warn('Connector snapshot missing type field; skipping schedule reconcile', {
        connectorId,
        responseKeys: Object.keys(envelope),
      });
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
    logger.warn('Failed to fetch connector snapshot for schedule reconcile', {
      connectorId,
      error: error instanceof Error ? error.message : 'Unknown error',
    });
    return null;
  }
};

/** Timeout (ms) for the background connector snapshot GET used by reconcile. */
const RECONCILE_SNAPSHOT_TIMEOUT_MS = 10_000;

/**
 * Sentinel returned by the timeout side of the Promise.race so we can
 * distinguish "timed out" from "fetch returned null (error / 404)".
 * Using a Symbol prevents any accidental equality with real return values.
 */
const SNAPSHOT_TIMEOUT = Symbol('SNAPSHOT_TIMEOUT');

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
    try {
      // Race the snapshot fetch against a hard timeout so a hung Python
      // backend cannot block this background task indefinitely.
      const timeoutPromise = new Promise<typeof SNAPSHOT_TIMEOUT>((resolve) =>
        setTimeout(() => resolve(SNAPSHOT_TIMEOUT), RECONCILE_SNAPSHOT_TIMEOUT_MS),
      );
      const result = await Promise.race([
        fetchConnectorSnapshot(req, connectorId, appConfig),
        timeoutPromise,
      ]);

      if (result === SNAPSHOT_TIMEOUT) {
        logger.warn('Connector snapshot fetch timed out; skipping schedule reconcile', {
          connectorId,
          timeoutMs: RECONCILE_SNAPSHOT_TIMEOUT_MS,
        });
        return;
      }

      // result is ConnectorSnapshot | null here (fetch completed, may have failed)
      const snapshot = result;
      if (!snapshot) {
        // fetchConnectorSnapshot already logged the reason (4xx, network error, etc.)
        return;
      }

      logger.debug('Connector snapshot fetched for schedule reconcile', {
        connectorId,
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
      await reconcileConnectorSchedule(scheduler, logger, input);
    } catch (error) {
      logger.error('Background schedule reconcile failed', {
        connectorId,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  });
};

export const isUserAdmin = async (req: AuthenticatedUserRequest): Promise<boolean> => {
  const { userId, orgId } = req.user || {};
  if (!userId) {
    throw new UnauthorizedError('User authentication required');
  }
  const groups = await UserGroups.find({
    orgId,
    users: { $in: [userId] },
    isDeleted: false,
  }).select('type');
  const isAdmin = groups.find((userGroup: any) => userGroup.type === 'admin');
  if (!isAdmin) {
    return false;
  }
  return true;
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

      logger.info(`Getting connector registry for user ${userId}`);

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

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };
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
      logger.error('Error getting connector registry', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, 'get connector registry');
      next(handledError);
    }
  };

/**
 * Get all configured connector instances.
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
      const { scope, page, limit, search } = req.query;
      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      if (!scope) {
        throw new BadRequestError('Scope is required');
      }

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

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

      logger.info(`Getting connector instances for user ${userId}`);

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/?${queryParams.toString()}`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector instances',
        'Connector instances not found'
      );
    } catch (error: any) {
      logger.error('Error getting connector instances', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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

      logger.info(`Getting active connector instances for user ${userId}`);

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/active`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting all active connectors',
        'Active connectors not found'
      );
    } catch (error: any) {
      logger.error('Error getting active connector instances', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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

      logger.info(`Getting inactive connector instances for user ${userId}`);

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/inactive`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting all inactive connectors',
        'Inactive connectors not found'
      );
    } catch (error: any) {
      logger.error('Error getting inactive connector instances', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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

      logger.info(`Getting configured connector instances for user ${userId}`);

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

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
      logger.error('Error getting configured connector instances', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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

      logger.info(`Creating connector instance for user ${userId}`, {
        connectorType,
        instanceName,
        authType,
      });

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

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
      logger.error('Error creating connector instance', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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

      logger.info(`Getting connector instance ${connectorId}`);
      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector instance',
        'Connector schema not found'
      );
    } catch (error: any) {
      logger.error('Error getting connector instance', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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

      logger.info(`Getting connector instance config for ${connectorId}`);

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/config`,
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
      logger.error('Error getting connector instance config', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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

      logger.info(`Updating connector instance config for ${connectorId}`);

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/config`,
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
      logger.error('Error updating connector instance config', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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
 * Delete a connector instance.
 *
 * We fetch the connector snapshot *before* issuing the DELETE so we still
 * know its `type` after Python removes it (a post-delete GET would 404).
 * On success we fire a background job removal so any active BullMQ
 * repeatable job does not outlive the connector.
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

      logger.info(`Deleting connector instance ${connectorId}`);

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      // Fetch snapshot before the DELETE so we still know the connector type
      // once Python has removed it.
      const snapshot = await fetchConnectorSnapshot(req, connectorId, appConfig);

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}`,
        HttpMethod.DELETE,
        headers,
      );

      const isSuccess =
        connectorResponse?.statusCode != null &&
        connectorResponse.statusCode >= 200 &&
        connectorResponse.statusCode < 300;

      // Remove any lingering BullMQ job in the background after a successful
      // delete. We need the connector type from the pre-delete snapshot; if
      // we could not fetch it we skip silently — worst case the job fires once
      // more and will encounter a 404 from the connector service.
      if (isSuccess && snapshot?.type) {
        const orgId = req.user?.orgId;
        if (orgId) {
          setImmediate(async () => {
            try {
              const existing = await scheduler.getJobStatus(
                snapshot.type,
                connectorId,
                orgId,
              );
              if (existing) {
                await scheduler.removeJob(snapshot.type, connectorId, orgId);
                logger.info('Removed BullMQ job after connector deletion', {
                  connectorId,
                  connectorType: snapshot.type,
                  orgId,
                });
              }
            } catch (err) {
              logger.error('Failed to remove BullMQ job after connector deletion', {
                connectorId,
                connectorType: snapshot.type,
                orgId,
                error: err instanceof Error ? err.message : 'Unknown error',
              });
            }
          });
        }
      }

      handleConnectorResponse(
        connectorResponse,
        res,
        'Deleting connector instance',
        'Connector instance not found'
      );
    } catch (error: any) {
      logger.error('Error deleting connector instance', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/name`,
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
      const { baseUrl } = req.query;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      const queryParams = new URLSearchParams();
      if (baseUrl) {
        queryParams.set('base_url', String(baseUrl));
      }

      const authorizationUrl = `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/oauth/authorize?${queryParams.toString()}`;

      logger.info(
        `Getting OAuth authorization URL for instance ${connectorId}`,
      );

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

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
      logger.error('Error getting OAuth authorization URL', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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

      logger.info('Handling OAuth callback');

      const queryParams = new URLSearchParams();
      if (code) queryParams.set('code', String(code));
      if (state) queryParams.set('state', String(state));
      if (error) queryParams.set('error', String(error));
      if (baseUrl) queryParams.set('base_url', String(baseUrl));

      const callbackUrl = `${appConfig.connectorBackend}/api/v1/connectors/oauth/callback?${queryParams.toString()}`;

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

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
          res.status(200).json({ redirectUrl: redirectUrlFromJson });
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
      logger.error('Error handling OAuth callback', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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

      logger.info(`Getting filter options for instance ${connectorId}`);

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/filters`,
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
      logger.error('Error getting connector instance filter options', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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
      const { page, limit, search, cursor } = req.query;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      if (!filterKey) {
        throw new BadRequestError('Filter key is required');
      }

      logger.info(`Getting filter field options for instance ${connectorId}, filter ${filterKey}`);

      const isAdmin = await isUserAdmin(req);
      logger.info(`User admin status: ${isAdmin} for userId: ${req.user?.userId}, orgId: ${req.user?.orgId}`);
      
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };
      
      logger.info(`Forwarding to Python with X-Is-Admin header: ${headers['X-Is-Admin']}`);

      // Build query string with cursor support
      const queryParams = new URLSearchParams();
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (search) queryParams.append('search', String(search));
      if (cursor) queryParams.append('cursor', String(cursor));
      const queryString = queryParams.toString() ? `?${queryParams.toString()}` : '';

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/filters/${filterKey}/options${queryString}`,
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
      logger.error('Error getting filter field options', {
        error: error.message,
        connectorId: req.params.connectorId,
        filterKey: req.params.filterKey,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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

      logger.info(`Saving filter options for instance ${connectorId}`);

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/filters`,
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
      logger.error('Error saving connector instance filter options', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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
      const { type, fullSync } = req.body;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      if (!type) {
        throw new BadRequestError('Toggle type is required');
      }

      logger.info(`Toggling connector instance ${connectorId} with type ${type}`);

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };
      const body: { type: string; fullSync?: boolean } = { type };
      if (typeof fullSync === 'boolean') {
        body.fullSync = fullSync;
      }
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/toggle`,
        HttpMethod.POST,
        headers,
        body,
      );

      const isSuccess =
        connectorResponse?.statusCode != null &&
        connectorResponse.statusCode >= 200 &&
        connectorResponse.statusCode < 300;

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
      logger.error('Error toggling connector instance', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
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

      logger.info(`Getting connector schema for ${connectorType}`);

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/registry/${connectorType}/schema`,
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
      logger.error('Error getting connector schema', {
        error: error.message,
        connectorType: req.params.connectorType,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, 'get connector schema');
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

    logger.info(`Getting connector registry for user ${userId}`);

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

    const isAdmin = await isUserAdmin(req);
    const headers: Record<string, string> = {
      ...(req.headers as Record<string, string>),
      'X-Is-Admin': isAdmin ? 'true' : 'false',
    };
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
    logger.error('Error getting active agent instances', {
      error: error.message,
      userId: req.user?.userId,
      status: error.response?.status,
      data: error.response?.data,
    });
    const handledError = handleBackendError(
      error,
      'get active agent instances',
    );
    next(handledError);
  }
};
