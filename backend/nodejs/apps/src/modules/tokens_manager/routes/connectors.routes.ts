/**
 * Connector Routes
 * 
 * RESTful routes for managing connector instances and configurations.
 * These routes follow the new instance-based architecture where multiple
 * instances of the same connector type can be created and managed independently.
 * 
 * @module connectors/routes
 */

import {
  Router,
  Request,
  RequestHandler,
  Response,
  NextFunction,
} from 'express';
import { createHash } from 'node:crypto';
import { Container } from 'inversify';
import { z } from 'zod';
import axios from 'axios';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { guardPathParams } from '../../../libs/middlewares/safe-path-params.middleware';
import { bindConnectorLogFields } from '../utils/connector.utils';
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import { isUserOrgAdmin } from '../../user_management/services/user-admin.service';
import { createKeyedRateLimiter } from '../../../libs/middlewares/rate-limit.middleware';
import { 
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest 
} from '../../../libs/middlewares/types';
import {
  BadRequestError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
} from '../../../libs/errors/http.errors';
import { withCause } from '../../../libs/errors/base.error';
import { markClientSafe } from '../../../libs/errors/reader-friendly';
import { VECTOR_STORE_REBUILD_AVAILABLE } from '../../../edition';
import { AppConfig, loadAppConfig } from '../config/config';
import { Logger } from '../../../libs/services/logger.service';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import {
  getConnectorRegistry,
  getConnectorInstances,
  getActiveConnectorInstances,
  getInactiveConnectorInstances,
  getConfiguredConnectorInstances,
  createConnectorInstance,
  getConnectorInstance,
  getConnectorInstanceConfig,
  updateConnectorInstanceConfig,
  updateConnectorInstanceAuthConfig,
  updateConnectorInstanceFiltersSyncConfig,
  deleteConnectorInstance,
  updateConnectorInstanceName,
  getOAuthAuthorizationUrl,
  handleOAuthCallback,
  getConnectorInstanceFilterOptions,
  getFilterFieldOptions,
  saveConnectorInstanceFilterOptions,
  toggleConnectorInstance,
  getConnectorSchema,
  testConnectorConnection,
  getConnectorEgressIps,
  getActiveAgentInstances,
  getConnectorStats,
  getPermissionStatus,
  setPermissionFilter,
  listMyPerUserConnectors,
  getMyConnectorConnection,
  connectMyConnectorAccount,
  startMyConnectorSignIn,
  disconnectMyConnectorAccount,
  listConnectorUserConnections,
  removeConnectorUserConnection,
  getConnectorUserRoster,
  inviteConnectorUsers,
  setMyConnectorReminders,
  proxyUserAccessLink,
  getRecordContent,
  navigateKnowledgeGraph,
  lookupRecord,
  cleanupVectorStore,
  reindexVectorStore,
  reindexConnector,
  resyncConnectorRecords,
  stopConnectorSync,
  explainPermission,
} from '../controllers/connector.controllers';
import { RecordRelationService } from '../../knowledge_base/services/kb.relation.service';
import { SyncEventProducer } from '../../knowledge_base/services/sync_events.service';
import { getConnectorsConfigRepository } from '../../../libs/repositories/connectors-config';
import { GoogleWorkspaceApp, scopeToAppMap } from '../types/connector.types';
import {
  GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH,
  GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH,
  REFRESH_TOKEN_PATH,
} from '../consts/constants';
import {
  getGoogleWorkspaceConfig,
  getGoogleWorkspaceIndividualCredentials,
  setGoogleWorkspaceIndividualCredentials,
  getRefreshTokenCredentials,
  getRefreshTokenConfig,
  setRefreshTokenCredentials,
} from '../services/connectors-config.service';
import { verifyGoogleWorkspaceToken } from '../utils/verifyToken';
import {
  AppEnabledEvent,
  EntitiesEventProducer,
  EventType,
  Event,
} from '../services/entity_event.service';
import { integerCheck } from '../../../libs/utils/zod-formats';
import { ConnectorId, ConnectorIdToNameMap } from '../../../libs/types/connector.types';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import {
  AllowlistOptions,
  DelegatedAllowlist,
  requireSessionAuthExcept,
} from '../../../libs/middlewares/require-session-auth.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { CrawlingSchedulerService } from '../../crawling_manager/services/crawling_service';

const logger = Logger.getInstance({
  service: 'ConnectorRoutes',
});

const GOOGLE_CLIENT_NOT_CONFIGURED =
  'Google Workspace client id or secret is not configured';

/** The sign-in code was used up by the exchange, so the reader starts the connect again rather than resending. */
export const SYNC_NOT_STARTED_MESSAGE =
  "We couldn't start syncing Google Workspace because PipesHub couldn't queue it. Nothing was synced. Connect Google Workspace again in a minute; if it keeps happening, ask your admin to check the services page.";

/** Asks the sync service to start; a lost event is the request's failure (503), never a silent success. */
const publishSyncStart = async (
  eventService: EntitiesEventProducer,
  event: Event,
): Promise<void> => {
  try {
    await eventService.publishEvent(event);
  } catch (error) {
    throw withCause(
      markClientSafe(new ServiceUnavailableError(SYNC_NOT_STARTED_MESSAGE)),
      error,
    );
  }
};

/**
 * A configuration-manager call that did not answer 200. A 5xx's status is the cause, so the error line
 * classes it as the service's (`upstream_5xx`, `unavailable`, `timeout`); any other answer stays our
 * `internal` failure (a 408 cause would read as `timeout`). Its body is never kept, since it can hold
 * stored credentials. The client still gets our 500.
 */
const failedCall = (
  message: string,
  response: { statusCode?: number },
): Error =>
  response.statusCode !== undefined && response.statusCode >= 500
    ? withCause(new InternalServerError(message), { status: response.statusCode })
    : new InternalServerError(message);

/** No refresh token from Google and none usable stored: only a new grant brings one. The app is the org's own Google client. */
export const NO_REFRESH_TOKEN_MESSAGE =
  "Google didn't send a refresh token, and PipesHub has no usable one saved for you. Remove this app's access in your Google account's third-party access settings (https://myaccount.google.com/permissions; it is listed under your organisation's Google app name), then connect Google Workspace again.";

/**
 * The refresh token to store with this consent's access token. Google sends one only on the first offline consent
 * for this client, so a repeat consent keeps the one stored for this PipesHub user, unless it has expired.
 */
const refreshTokenFor = async (
  req: AuthenticatedUserRequest,
  tokenData: { refresh_token?: string; refresh_token_expires_in?: number },
  config: AppConfig,
): Promise<{ refreshToken: string; refreshTokenExpiry?: number }> => {
  // Google's lifetime describes the grant's refresh token: the new one, or the stored one it did not resend.
  const googleExpiry = tokenData.refresh_token_expires_in
    ? tokenData.refresh_token_expires_in * 1000 + Date.now()
    : undefined;
  if (tokenData.refresh_token) {
    return {
      refreshToken: tokenData.refresh_token,
      refreshTokenExpiry: googleExpiry,
    };
  }
  const stored = await getGoogleWorkspaceIndividualCredentials(
    req,
    config.cmBackend,
    config.scopedJwtSecret,
  );
  if (stored.statusCode !== 200) {
    throw failedCall(
      'Error reading the stored Google Workspace credentials',
      stored,
    );
  }
  const {
    refresh_token: storedToken,
    refresh_token_expiry_time: storedExpiry,
  } = (stored.data ?? {}) as {
    refresh_token?: string;
    refresh_token_expiry_time?: unknown;
  };
  // Only an absent expiry means "does not expire"; 0, NaN or a non-number is not a time still ahead.
  const expiryAhead =
    typeof storedExpiry === 'number' && storedExpiry > Date.now();
  const expiryAbsent = storedExpiry === undefined || storedExpiry === null;
  if (!storedToken || !(expiryAbsent || expiryAhead)) {
    throw new BadRequestError(NO_REFRESH_TOKEN_MESSAGE);
  }
  logger.info('Google sent no refresh token; keeping the stored one');
  return {
    refreshToken: storedToken,
    refreshTokenExpiry:
      googleExpiry ?? (expiryAhead ? storedExpiry : undefined),
  };
};

/**
 * What a token may send when it changes or creates a connector: names, sync and filter
 * settings, and which sign-in form the connector uses. Everything else the controllers read
 * (`auth`, `baseUrl`, `oauthConfigId`, and any key added later) holds a credential, links an
 * OAuth app or names the host credentials are sent to, so it takes an interactive session.
 * Allowed keys, and why each is safe:
 *  - `sync`, `filters`: schedules and what to index; neither holds a credential or a host.
 *  - `connectorType`, `instanceName`, `scope`: which connector, what to call it, who owns it.
 *  - `authType`: picks the sign-in form; the credentials for it are in `auth`, which is refused.
 *  - `connectorId`: which saved connector the connection check reads stored settings from.
 *  - `config` (create): only its `sync` and `filters`; `config.auth` is refused.
 */
const CONNECTOR_UPDATE_ALLOWED: DelegatedAllowlist = {
  sync: true,
  filters: true,
};
const CONNECTOR_AUTH_UPDATE_ALLOWED: DelegatedAllowlist = {};
const CONNECTOR_CREATE_ALLOWED: DelegatedAllowlist = {
  connectorType: true,
  instanceName: true,
  scope: true,
  authType: true,
  config: ['sync', 'filters'],
};
const CONNECTOR_TEST_ALLOWED: DelegatedAllowlist = {
  authType: true,
  connectorId: true,
};

// An off-list key is refused whenever it is present, even empty, because an empty `auth` on a
// save route clears what is stored. These are the empty ones a real caller sends and that store
// nothing: the SDK connection-check examples send `auth: {}` (and the check's schema requires
// `auth`), and mcp-server `add_web_source` and the SDK create examples send `config: { auth: {} }`.
const EMPTY_AUTH_OK: AllowlistOptions = { emptyAllowed: ['auth'] };
const EMPTY_CONFIG_AUTH_OK: AllowlistOptions = {
  emptyAllowed: ['config.auth'],
};

// ============================================================================
// Validation Schemas
// ============================================================================

/**
 * Schema for creating a new connector instance
 */
const createConnectorInstanceSchema = z.object({
  body: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
    instanceName: z.string().min(1, 'Instance name is required'),
    config: z.object({
      auth: z.any().optional(),
      sync: z.any().optional(),
      filters: z.any().optional(),
    }).optional(),
    baseUrl: z.string().optional(),
    scope: z.enum(['team', 'personal']).refine((val) => val === 'team' || val === 'personal', {
      message: 'Scope must be either team or personal',
    }),
    authType: z.string().optional(), // Auth type selected by user (required for connectors with multiple auth types)
  }),
});

/**
 * Schema for validating connectorId parameter.
 * The pattern bounds shape and forbids URL-structural characters
 * (slashes, dots, percent-encoding) to keep the value safe for
 * interpolation into downstream service URL paths.
 */
const connectorIdParamSchema = z.object({
  params: z.object({
    connectorId: z
      .string()
      .regex(
        /^[A-Za-z0-9_-]{1,64}$/,
        'Connector ID must be 1-64 chars of letters, digits, underscore, or hyphen',
      ),
  }),
});

const permissionFilterSchema = connectorIdParamSchema.extend({
  body: z.object({ mode: z.enum(['inherit', 'legacy', 'scopes']) }).strict(),
});

/** A user connecting their own source account to a per-user team connector. */
const connectMyAccountSchema = connectorIdParamSchema.extend({
  body: z.object({ token: z.string().trim().min(1).max(2000) }),
});

const connectorUserParamSchema = z.object({
  params: connectorIdParamSchema.shape.params.extend({
    userId: z
      .string()
      .regex(
        /^[A-Za-z0-9_-]{1,64}$/,
        'User ID must be 1-64 chars of letters, digits, underscore, or hyphen',
      ),
  }),
});

const inviteConnectorUsersSchema = connectorIdParamSchema.extend({
  body: z.union([
    z.strictObject({
      userIds: z.array(z.string().trim().min(1).max(128)).min(1).max(500),
    }),
    z.strictObject({ all: z.literal(true) }),
  ]),
});

const toNumber = (arg: unknown): unknown =>
  arg === '' || arg === undefined ? undefined : Number(arg);

/** One page of the roster, optionally only people whose name or email contains `q`. */
const connectorRosterSchema = connectorIdParamSchema.extend({
  query: z.object({
    q: z.string().max(200).optional(),
    offset: z
      .preprocess(toNumber, z.number().check(integerCheck()).min(0))
      .optional(),
    limit: z
      .preprocess(toNumber, z.number().check(integerCheck()).min(1).max(100))
      .optional(),
  }),
});

const connectorRemindersSchema = connectorIdParamSchema.extend({
  body: z.object({ optedOut: z.boolean() }),
});

/** The emailed connect link: its token is the only credential these public routes take. */
const connectLinkTokenSchema = z.object({
  body: z.object({ token: z.string().min(1).max(300) }),
});

const connectLinkCompleteSchema = z.object({
  body: z.object({
    code: z.string().min(1).max(4000),
    state: z.string().min(1).max(4000),
  }),
});

// Per link, so a team behind one address can all open their emails at once; the per-address
// ceiling bounds someone trying many links.
const CONNECT_LINK_PER_LINK_PER_MINUTE = 20;
const CONNECT_LINK_PER_IP_PER_MINUTE = 300;

/** The link a public connect-link call is about (token, or OAuth state), hashed so no key or log holds it. */
const connectLinkSubKey =
  (field: 'token' | 'state') =>
  (req: Request): string | undefined => {
    const value: unknown = (req.body as Record<string, unknown> | undefined)?.[
      field
    ];
    return typeof value === 'string' && value !== ''
      ? createHash('sha256').update(value).digest('hex').slice(0, 32)
      : undefined;
  };

/**
 * Schema for updating connector instance configuration
 */
const updateConnectorInstanceConfigSchema = z.object({
  body: z.object({
    auth: z.any().optional(),
    sync: z.any().optional(),
    filters: z.any().optional(),
    baseUrl: z.string().optional(),
  }),
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
});

/**
 * Schema for updating connector instance auth configuration
 */
const updateConnectorInstanceAuthConfigSchema = z.object({
  body: z.object({
    auth: z.any().optional(),
    baseUrl: z.string().optional(),
  }),
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
});

/**
 * Schema for updating connector instance filters and sync configuration
 */
const updateConnectorInstanceFiltersSyncConfigSchema = z.object({
  body: z.object({
    sync: z.any().optional(),
    filters: z.any().optional(),
  }),
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
});

/**
 * Schema for getting OAuth authorization URL
 */
const getOAuthAuthorizationUrlSchema = z.object({
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
  query: z.object({
    baseUrl: z.string().optional(),
    desktop: z.string().optional(),
  }),
});

/**
 * Schema for handling OAuth callback
 */
const handleOAuthCallbackSchema = z.object({
  query: z.object({
    baseUrl: z.string().optional(),
    code: z.string().optional(),
    state: z.string().optional(),
    error: z.string().optional(),
  }),
});

/**
 * Schema for saving connector instance filter options
 */
const saveConnectorInstanceFilterOptionsSchema = z.object({
  body: z.object({
    filters: z.any().optional(),
  }),
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
});

// The connector service pages filter options 100 at a time at most.
const FILTER_OPTIONS_LIMIT_MESSAGE = 'Limit must be between 1 and 100.';

/**
 * Schema for getting filter field options (dynamic with pagination)
 */
const getFilterFieldOptionsSchema = z.object({
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
    filterKey: z.string().min(1, 'Filter key is required'),
  }),
  query: z.object({
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().check(integerCheck()).min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().check(integerCheck()).min(1, FILTER_OPTIONS_LIMIT_MESSAGE).max(100, FILTER_OPTIONS_LIMIT_MESSAGE))
      .optional(),
    search: z.string().optional(),
    cursor: z.string().optional(),
    contextGroupPath: z
      .union([z.string(), z.array(z.string())])
      .optional()
      .transform((val) => {
        if (val === undefined || val === null) return undefined;
        return Array.isArray(val) ? val : [val];
      }),
    excludeContextGroupPath: z
      .union([z.string(), z.array(z.string())])
      .optional()
      .transform((val) => {
        if (val === undefined || val === null) return undefined;
        return Array.isArray(val) ? val : [val];
      }),
  }),
});

/**
 * Schema for validating connector toggle type parameter
 */
const connectorToggleSchema = z.object({
  body: z.object({
    type: z.enum(['sync', 'agent']),
    // The state wanted. Without it the connector service flips the current
    // state, which a retried request undoes.
    enabled: z.boolean().optional(),
    fullSync: z.boolean().optional(),
    deviceId: z.string().min(1).max(255).optional(),
    deviceName: z.string().max(255).optional(),
  }),
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
});
/**
 * Schema for validating connector type parameter
 */
const connectorTypeParamSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
  }),
});

/**
 * Schema for validating connector list query parameters.
 * Covers both GET / (instances) and GET /registry endpoints.
 * The three filter params (is_authenticated, is_active, connector_type) are
 * only meaningful on the instances endpoint but are declared here to keep
 * a single reusable schema.
 */
const connectorListSchema = z.object({
  query: z.object({
    scope: z
      .enum(['team', 'personal'])
      .refine((val) => val === 'team' || val === 'personal', {
        message: 'Scope must be either team or personal',
      })
      .default('team'),
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().check(integerCheck()).min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().check(integerCheck()).min(1).max(200))
      .optional(),
    search: z.string().optional(),
    /** Filter instances by authentication status (instances endpoint only). */
    isAuthenticated: z
      .preprocess(
        (arg) => {
          if (arg === 'true') return true;
          if (arg === 'false') return false;
          return arg;
        },
        z.boolean(),
      )
      .optional(),
    /** Filter instances by active status (instances endpoint only). */
    isActive: z
      .preprocess(
        (arg) => {
          if (arg === 'true') return true;
          if (arg === 'false') return false;
          return arg;
        },
        z.boolean(),
      )
      .optional(),
    /** Filter instances by exact connector type (instances endpoint only). */
    connectorType: z.string().min(1).optional(),
  }),
});


/**
 * Schema for resyncing connector records
 */
const resyncConnectorSchema = z.object({
  params: z.object({ connectorId: z.string().min(1) }),
  body: z.object({
    connectorName: z.string().min(1),
    fullSync: z.boolean().optional(),
  }),
});

/**
 * Schema for reindexing connector records
 */
export const REINDEX_STAGES = ['classify', 'entities'] as const;

export const reindexConnectorSchema = z.object({
  params: z.object({ connectorId: z.string().min(1) }),
  body: z.object({
      statusFilters: z.array(z.string()).optional(),
      stages: z.array(z.enum(REINDEX_STAGES)).min(1).optional(),
    })
    .optional(),
});

/**
 * Schema for getting connector stats
 */
const getConnectorStatsSchema = z.object({
  params: z.object({ connectorId: z.string().min(1) }),
});

/**
 * Schema for getting a record's parsed content. With no query parameters the
 * whole record is returned, as it always was; any of them asks for a budgeted
 * read (part of the record, or its Slack thread). Bounds match the query
 * service's `/records/{record_id}/read`.
 */
const getRecordContentSchema = z.object({
  params: z.object({ recordId: z.string().min(1) }),
  query: z.object({
    mode: z.enum(['record', 'thread']).optional(),
    query: z.string().trim().min(1).max(2000).optional(),
    startBlock: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? undefined : Number(arg)),
        z.number().check(integerCheck()).min(0),
      )
      .optional(),
    startRecord: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? undefined : Number(arg)),
        z.number().check(integerCheck()).min(0),
      )
      .optional(),
    maxChars: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? undefined : Number(arg)),
        z.number().check(integerCheck()).min(2000).max(140000),
      )
      .optional(),
    // With `query`: only the passages that answer it, picked out by a model.
    extract: z.enum(['true', 'false']).optional(),
  }),
});

/**
 * Schema for walking the knowledge graph hierarchy
 */
const navigateKnowledgeGraphSchema = z.object({
  query: z.object({
    // Deliberately not `.uuid()`: a URL or an issue key (PA-1787) is resolved
    // to its record before navigating, so those must be accepted here.
    nodeId: z.string().min(1).max(2048).optional(),
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().check(integerCheck()).min(1))
      .optional(),
    // Floor of 50 matches GraphNavigator._MIN_LIMIT — anything lower would be
    // silently raised, so reject it here rather than ignore it.
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().check(integerCheck()).min(50).max(200))
      .optional(),
    depth: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().check(integerCheck()).min(1).max(3))
      .optional(),
    nodeTypes: z
      .union([z.string(), z.array(z.string())])
      .optional()
      .transform((val) => {
        if (val === undefined || val === null) return undefined;
        return Array.isArray(val) ? val : [val];
      }),
    createdAfter: z.string().min(1).optional(),
    createdBefore: z.string().min(1).optional(),
    modifiedAfter: z.string().min(1).optional(),
    modifiedBefore: z.string().min(1).optional(),
    cursor: z.string().min(1).max(4096).optional(),
  }),
});

/**
 * Schema for resolving URLs / issue keys / external IDs to Record IDs
 */
const lookupRecordSchema = z.object({
  query: z.object({
    identifiers: z
      .union([z.string().min(1).max(2048), z.array(z.string().min(1).max(2048))])
      .transform((val) => (Array.isArray(val) ? val : [val]))
      .refine((val) => val.length >= 1 && val.length <= 10, {
        message: 'Provide between 1 and 10 identifiers',
      }),
    connectorName: z.string().min(1).optional(),
  }),
});

/**
 * Why a person can or cannot read a record. Ids only: no search through titles.
 */
const explainPermissionSchema = z.object({
  params: z.object({ connectorId: z.string().min(1).max(128) }),
  query: z.object({
    userId: z.string().min(1).max(128),
    recordId: z.string().min(1).max(256),
  }),
});

/** An admin-only route a non-admin cannot tell from one that does not exist: the 404 of any unknown route. */
const adminOrNotFound = async (
  req: AuthenticatedUserRequest,
  _res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const user = req.user as { userId?: string; orgId?: string } | undefined;
    const userId = user?.userId;
    const orgId = user?.orgId;
    if (!userId || !orgId || !(await isUserOrgAdmin(userId, orgId))) {
      throw new NotFoundError('Route not found');
    }
    next();
  } catch (error) {
    next(error);
  }
};

const EXPLAIN_PER_ADMIN_PER_MINUTE = 30;

const testConnectorConnectionSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
  }),
  body: z.object({
    auth: z.record(z.string(), z.unknown()),
    // The chosen form when a connector offers several: `auth` holds only its fields.
    authType: z.string().min(1).max(64).optional(),
    connectorId: z
      .string()
      .regex(
        /^[A-Za-z0-9_-]{1,64}$/,
        'Connector ID must be 1-64 chars of letters, digits, underscore, or hyphen',
      )
      .optional(),
  }),
});

// ============================================================================
// Router Factory
// ============================================================================

/**
 * Create and configure the connector router.
 *
 * @param container - Tokens-manager DI container for existing connector route
 *   dependencies: auth, app config, entity events, metrics, and storage.
 * @param crawlingContainer - Crawling-manager DI container for the scheduler.
 * @returns Configured Express router
 */
/**
 * Answers 403 on the vector-store rebuild routes when this deployment does not
 * offer them (`VECTOR_STORE_REBUILD_AVAILABLE` in `edition.ts`). The routes stay
 * registered either way, so they are matched before `/:connectorId/...`.
 */
export const requireVectorStoreRebuild = (
  available: boolean = VECTOR_STORE_REBUILD_AVAILABLE,
) =>
  function vectorStoreRebuildGuard(
    _req: unknown,
    _res: Response,
    next: NextFunction,
  ): void {
    if (available) {
      next();
      return;
    }
    next(
      new ForbiddenError(
        'Rebuilding the vector store is not available on this deployment.',
      ),
    );
  };

export function createConnectorRouter(
  container: Container,
  crawlingContainer: Container,
): Router {
  const router = Router();
  guardPathParams(
    router,
    'connectorId',
    'connectorType',
    'filterKey',
    'recordId',
  );
  bindConnectorLogFields(router);
  let config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const eventService = container.get<EntitiesEventProducer>('EntitiesEventProducer');
  const scheduler = crawlingContainer.get<CrawlingSchedulerService>(
    CrawlingSchedulerService,
  );
  const syncEventProducer =
    container.get<SyncEventProducer>('SyncEventProducer');
  const recordRelationService = new RecordRelationService(
    syncEventProducer,
    config.storage,
  );

  // ============================================================================
  // Registry Routes
  // ============================================================================

  /**
   * GET /registry
   * Get all available connector types from registry
   */
  router.get(
    '/registry',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(connectorListSchema),
    getConnectorRegistry(config)
  );

  /**
   * GET /registry/:connectorType/schema
   * Get connector schema for a specific type
   */
  router.get(
    '/registry/:connectorType/schema',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(connectorTypeParamSchema),
    getConnectorSchema(config)
  );

  /**
   * POST /registry/:connectorType/test-connection
   * Try auth settings from the setup form before they are saved
   */
  router.post(
    '/registry/:connectorType/test-connection',
    authMiddleware.authenticate,
    requireSessionAuthExcept(CONNECTOR_TEST_ALLOWED, EMPTY_AUTH_OK),
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(testConnectorConnectionSchema),
    testConnectorConnection(config)
  );

  /**
   * GET /network/egress-ips
   * Public IPs connectors connect from, to allow in a firewall
   */
  router.get(
    '/network/egress-ips',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    getConnectorEgressIps(config)
  );

  // ============================================================================
  // Instance Management Routes
  // ============================================================================

  /**
   * GET /instances
   * Get all configured connector instances
   */
  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(connectorListSchema),
    getConnectorInstances(config)
  );

  /**
   * POST /instances
   * Create a new connector instance
   */
  router.post(
    '/',
    authMiddleware.authenticate,
    // A token may create a connector without credentials; setting them, or linking an OAuth
    // app, takes an interactive session.
    requireSessionAuthExcept(CONNECTOR_CREATE_ALLOWED, EMPTY_CONFIG_AUTH_OK),
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(createConnectorInstanceSchema),
    createConnectorInstance(config)
  );

  /**
   * GET /instances/active
   * Get all active connector instances
   */
  router.get(
    '/active',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    getActiveConnectorInstances(config)
  );

  /**
   * GET /instances/inactive
   * Get all inactive connector instances
   */
  router.get(
    '/inactive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    getInactiveConnectorInstances(config)
  );

  /**
   * GET /instances/agents/active
   * Get all active agent instances
   */
  router.get(
    '/agents/active',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(connectorListSchema),
    getActiveAgentInstances(config)
  );
  /**
   * GET /instances/configured
   * Get all configured connector instances
   */
  router.get(
    '/configured',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(connectorListSchema),
    getConfiguredConnectorInstances(config)
  );

  // ============================================================================
  // Knowledge Graph Routes
  //
  // `/navigate` is a single segment, so it must stay above `/:connectorId`
  // below or Express matches it as a connector id. `/record/lookup` is kept
  // alongside it so the ordering constraint reads as one block.
  // ============================================================================

  /**
   * GET /navigate
   * Walk the knowledge graph hierarchy: App -> RecordGroup -> Record -> Child
   */
  router.get(
    '/navigate',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ, OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(navigateKnowledgeGraphSchema),
    navigateKnowledgeGraph(config),
  );

  /**
   * GET /record/lookup
   * Resolve URLs, issue keys, or external IDs to Record IDs
   */
  router.get(
    '/record/lookup',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.KB_READ, OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(lookupRecordSchema),
    lookupRecord(config),
  );

  /**
   * GET /:connectorId/permissions/explain
   * Why a person can or cannot read a record. Org admins only; anyone else gets a 404.
   */
  router.get(
    '/:connectorId/permissions/explain',
    (req, res, next) => authMiddleware.authenticate(req, res, next),
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    adminOrNotFound,
    createKeyedRateLimiter(logger, {
      prefix: 'permission-explain',
      maxRequestsPerMinute: EXPLAIN_PER_ADMIN_PER_MINUTE,
      message: 'Too many access checks. Try again in a minute.',
    }),
    ValidationMiddleware.validate(explainPermissionSchema),
    explainPermission(config),
  );

  /**
   * GET /instances/:connectorId
   * Get a specific connector instance
   */
  router.get(
    '/:connectorId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(connectorIdParamSchema),
    getConnectorInstance(config)
  );

  /**
   * DELETE /instances/:connectorId
   * Delete a connector instance
   */
  router.delete(
    '/:connectorId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_DELETE),
    ValidationMiddleware.validate(connectorIdParamSchema),
    deleteConnectorInstance(config, scheduler)
  );

  // ============================================================================
  // Stats & Sync Routes
  // ============================================================================

  /**
   * GET /:connectorId/stats
   * Get indexing stats for a connector instance
   */
  router.get(
    '/:connectorId/stats',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ, OAuthScopeNames.KB_READ),
    ValidationMiddleware.validate(getConnectorStatsSchema),
    getConnectorStats(config),
  );

  /**
   * GET /:connectorId/permissions/status
   * How fresh the connector's permissions are. Admin only: the connector service answers
   * anyone else with the 404 of a missing connector.
   */
  router.get(
    '/:connectorId/permissions/status',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(connectorIdParamSchema),
    getPermissionStatus(config),
  );

  /**
   * PATCH /:connectorId/permissions/filter
   * The connector's own permission filter mode (inherit, legacy or scopes). Admin only, decided by the
   * connector service as for the status.
   */
  router.patch(
    '/:connectorId/permissions/filter',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(permissionFilterSchema),
    setPermissionFilter(config),
  );

  /**
   * Per-user access: each user connects their own source account to a team connector in
   * per-user mode. Any member of the organisation may manage their own connection; the
   * connector service answers anyone else, and any other connector, with a 404.
   */
  router.get(
    '/user-access/mine',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    listMyPerUserConnectors(config),
  );

  /**
   * Public: the page behind an emailed connect link. No login; the link token is the
   * credential, checked by the connector service. Counted per link and per address on top
   * of the global limit, since anyone can call these.
   */
  const connectLinkPerIp = createKeyedRateLimiter(logger, {
    prefix: 'connector-connect-link-ip',
    maxRequestsPerMinute: CONNECT_LINK_PER_IP_PER_MINUTE,
    message: 'Too many requests. Try again in a minute.',
  });
  const connectLinkPerLink = (field: 'token' | 'state'): RequestHandler =>
    createKeyedRateLimiter(logger, {
      prefix: `connector-connect-link-${field}`,
      maxRequestsPerMinute: CONNECT_LINK_PER_LINK_PER_MINUTE,
      message: 'Too many requests for this link. Try again in a minute.',
      anonymousSubKey: connectLinkSubKey(field),
    });
  const connectLinkPerToken = connectLinkPerLink('token');
  for (const name of ['info', 'authorize', 'opt-out'] as const) {
    router.post(
      `/user-access/link/${name}`,
      connectLinkPerIp,
      connectLinkPerToken,
      ValidationMiddleware.validate(connectLinkTokenSchema),
      proxyUserAccessLink(config, name),
    );
  }
  router.post(
    '/user-access/link/complete',
    connectLinkPerIp,
    connectLinkPerLink('state'),
    ValidationMiddleware.validate(connectLinkCompleteSchema),
    proxyUserAccessLink(config, 'complete'),
  );

  router.get(
    '/:connectorId/user-access',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(connectorIdParamSchema),
    getMyConnectorConnection(config),
  );

  router.post(
    '/:connectorId/user-access',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(connectMyAccountSchema),
    connectMyConnectorAccount(config),
  );

  router.get(
    '/:connectorId/user-access/oauth/authorize',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(connectorIdParamSchema),
    startMyConnectorSignIn(config),
  );

  router.delete(
    '/:connectorId/user-access',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(connectorIdParamSchema),
    disconnectMyConnectorAccount(config),
  );

  /** Admin only: who has connected, and removing someone's connection. A non-admin gets a 404. */
  router.get(
    '/:connectorId/user-access/users',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    adminOrNotFound,
    ValidationMiddleware.validate(connectorIdParamSchema),
    listConnectorUserConnections(config),
  );

  router.delete(
    '/:connectorId/user-access/users/:userId',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    adminOrNotFound,
    ValidationMiddleware.validate(connectorUserParamSchema),
    removeConnectorUserConnection(config),
  );

  /** Admin only: everyone who could connect, and asking them to. A non-admin gets a 404. */
  router.get(
    '/:connectorId/user-access/roster',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    adminOrNotFound,
    ValidationMiddleware.validate(connectorRosterSchema),
    getConnectorUserRoster(config),
  );

  router.post(
    '/:connectorId/user-access/invites',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    adminOrNotFound,
    ValidationMiddleware.validate(inviteConnectorUsersSchema),
    inviteConnectorUsers(config),
  );

  /** The caller's own "don't remind me" for this connector. */
  router.post(
    '/:connectorId/user-access/reminders',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(connectorRemindersSchema),
    setMyConnectorReminders(config),
  );

  /**
   * GET /record/:recordId/content
   * Get full parsed content + metadata for a record
   */
  router.get(
    '/record/:recordId/content',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(getRecordContentSchema),
    getRecordContent(config),
  );

  /**
   * POST /vector-store/cleanup
   * Drop and recreate the shared records vector collection. Admin only.
   */
  router.post(
    '/vector-store/cleanup',
    authMiddleware.authenticate,
    requireVectorStoreRebuild(),
    userAdminCheck,
    requireScopes(OAuthScopeNames.CONNECTOR_SYNC),
    cleanupVectorStore(config),
  );

  /**
   * POST /vector-store/reindex
   * Re-embed every connector from blob storage. Admin only. Does not drop the collection.
   */
  router.post(
    '/vector-store/reindex',
    authMiddleware.authenticate,
    requireVectorStoreRebuild(),
    userAdminCheck,
    requireScopes(OAuthScopeNames.CONNECTOR_SYNC),
    reindexVectorStore(config),
  );

  /**
   * POST /:connectorId/reindex
   * Reindex all records for a connector instance (optionally filtered by status).
   * Covers both external connectors and KB app instances (a KB is itself a
   * connector instance).
   */
  router.post(
    '/:connectorId/reindex',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_SYNC, OAuthScopeNames.KB_WRITE),
    ValidationMiddleware.validate(reindexConnectorSchema),
    reindexConnector(config),
  );

  /**
   * POST /:connectorId/resync
   * Resync connector records
   */
  router.post(
    '/:connectorId/resync',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE, OAuthScopeNames.KB_WRITE),
    ValidationMiddleware.validate(resyncConnectorSchema),
    resyncConnectorRecords(recordRelationService, config),
  );

  /**
   * POST /:connectorId/sync/stop
   * Request cancellation of the in-flight sync for a connector.
   */
  router.post(
    '/:connectorId/sync/stop',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_SYNC),
    ValidationMiddleware.validate(connectorIdParamSchema),
    stopConnectorSync(config),
  );

  // ============================================================================
  // Configuration Routes
  // ============================================================================

  /**
   * GET /instances/:connectorId/config
   * Get configuration for a connector instance
   */
  router.get(
    '/:connectorId/config',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(connectorIdParamSchema),
    getConnectorInstanceConfig(config)
  );

  /**
   * PUT /instances/:connectorId/config
   * Update configuration for a connector instance
   */
  router.put(
    '/:connectorId/config',
    authMiddleware.authenticate,
    // A token may change sync and filter settings; anything else takes an interactive session.
    requireSessionAuthExcept(CONNECTOR_UPDATE_ALLOWED),
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(updateConnectorInstanceConfigSchema),
    updateConnectorInstanceConfig(config, scheduler)
  );

  /**
   * PUT /instances/:connectorId/config/auth
   * Update authentication configuration for a connector instance
   */
  router.put(
    '/:connectorId/config/auth',
    authMiddleware.authenticate,
    // A token may send no key here: credential changes take an interactive session.
    requireSessionAuthExcept(CONNECTOR_AUTH_UPDATE_ALLOWED),
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(updateConnectorInstanceAuthConfigSchema),
    updateConnectorInstanceAuthConfig(config)
  );

  /**
   * PUT /instances/:connectorId/config/filters-sync
   * Update filters and sync configuration for a connector instance
   */
  router.put(
    '/:connectorId/config/filters-sync',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(updateConnectorInstanceFiltersSyncConfigSchema),
    updateConnectorInstanceFiltersSyncConfig(config, scheduler)
  );

  /**
   * PUT /instances/:connectorId/name
   * Update connector instance name
   */
  router.put(
    '/:connectorId/name',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(
      z.object({
        body: z.object({ instanceName: z.string().min(1, 'Instance name is required') }),
        params: z.object({ connectorId: z.string().min(1, 'Connector ID is required') })
      })
    ),
    updateConnectorInstanceName(config)
  );

  // ============================================================================
  // OAuth Routes
  // ============================================================================

  /**
   * GET /instances/:connectorId/oauth/authorize
   * Get OAuth authorization URL for a connector instance
   */
  router.get(
    '/:connectorId/oauth/authorize',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(getOAuthAuthorizationUrlSchema),
    getOAuthAuthorizationUrl(config)
  );

  /**
   * GET /oauth/callback
   * Handle OAuth callback (connector_id is encoded in state parameter)
   */
  router.get(
    '/oauth/callback',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(handleOAuthCallbackSchema),
    handleOAuthCallback(config)
  );

  // ============================================================================
  // Filter Routes
  // ============================================================================

  /**
   * GET /instances/:connectorId/filters
   * Get filter options for a connector instance
   */
  router.get(
    '/:connectorId/filters',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(connectorIdParamSchema),
    getConnectorInstanceFilterOptions(config)
  );

  /**
   * POST /instances/:connectorId/filters
   * Save filter selections for a connector instance
   * @deprecated Use PUT /:connectorId/config/filters-sync; nothing in the app calls this.
   */
  router.post(
    '/:connectorId/filters',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    ValidationMiddleware.validate(saveConnectorInstanceFilterOptionsSchema),
    saveConnectorInstanceFilterOptions(config)
  );

  /**
   * GET /instances/:connectorId/filters/:filterKey/options
   * Get dynamic filter field options with pagination for a specific filter
   */
  router.get(
    '/:connectorId/filters/:filterKey/options',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    ValidationMiddleware.validate(getFilterFieldOptionsSchema),
    getFilterFieldOptions(config)
  );

  // ============================================================================
  // Toggle Route
  // ============================================================================

  /**
   * POST /instances/:connectorId/toggle
   * Toggle connector instance active status
   */
  router.post(
    '/:connectorId/toggle',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_SYNC),
    ValidationMiddleware.validate(connectorToggleSchema),
    toggleConnectorInstance(config, scheduler)
  );

  // ============================================================================
  // Legacy Routes (Backward Compatibility)
  // ============================================================================

  /**
   * @deprecated Use / instead
   * GET /
   * Get all connector instances (backward compatibility)
   */
  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    getConnectorInstances(config)
  );

  /**
   * @deprecated Use /active instead
   * GET /active
   * Get active connector instances (backward compatibility)
   */
  router.get(
    '/active',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    getActiveConnectorInstances(config)
  );

  /**
   * @deprecated Use /inactive instead
   * GET /inactive
   * Get inactive connector instances (backward compatibility)
   */
  router.get(
    '/inactive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    getInactiveConnectorInstances(config)
  );

  // ============================================================================
  // Legacy Google Workspace Routes
  // ============================================================================

  /**
   * @deprecated Legacy Google Workspace token exchange endpoint
   * POST /getTokenFromCode
   * Exchange authorization code for access token (Google Workspace specific)
   */
  router.post(
    '/getTokenFromCode',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    userAdminCheck,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        if (!req.user) {
          throw new NotFoundError('User not found');
        }

        // Get Google Workspace configuration
        const configResponse = await getGoogleWorkspaceConfig(
          req,
          config.cmBackend,
          config.scopedJwtSecret,
        );

        if (configResponse.statusCode !== 200) {
          throw failedCall('Error getting Google Workspace config', configResponse);
        }

        const configData = configResponse.data;
        if (!configData.clientId || !configData.clientSecret) {
          throw new InternalServerError(GOOGLE_CLIENT_NOT_CONFIGURED);
        }

        const enableRealTimeUpdates = configData?.enableRealTimeUpdates;
        const topicName = configData?.topicName;

        // Get frontend base URL and construct redirect URI
        const appConfig = await loadAppConfig();
        const frontendBaseUrl = appConfig.frontendUrl;
        const redirectUri = frontendBaseUrl.endsWith('/')
          ? `${frontendBaseUrl}account/individual/settings/connector/googleWorkspace`
          : `${frontendBaseUrl}/account/individual/settings/connector/googleWorkspace`;

        // Exchange authorization code for tokens
        const googleResponse = await axios.post(
          GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH,
          {
            code: req.body.tempCode,
            client_id: configData.clientId,
            client_secret: configData.clientSecret,
            redirect_uri: redirectUri,
            grant_type: 'authorization_code',
          },
        );

        if (googleResponse.status !== 200) {
          throw new BadRequestError('Error exchanging authorization code');
        }

        const tokenData = googleResponse.data;

        // Verify ID token
        verifyGoogleWorkspaceToken(req, tokenData?.id_token);

        const { refreshToken, refreshTokenExpiry } = await refreshTokenFor(
          req,
          tokenData,
          config,
        );

        // Store credentials
        const credentialsResponse = await setGoogleWorkspaceIndividualCredentials(
          req,
          config.cmBackend,
          config.scopedJwtSecret,
          tokenData.access_token,
          refreshToken,
          tokenData.expires_in * 1000 + Date.now(),
          refreshTokenExpiry,
          enableRealTimeUpdates,
          topicName,
        );

        if (credentialsResponse.statusCode !== 200) {
          throw failedCall('Error storing access token', credentialsResponse);
        }

        const connectorId = ConnectorId.GOOGLE_WORKSPACE;
        if (!connectorId) {
          throw new NotFoundError(
            'Google Workspace connector not found in config',
          );
        }

        // Find or create connector in database
        const connectorsConfig = getConnectorsConfigRepository();
        const connectorName = ConnectorIdToNameMap[connectorId];
        let connector = await connectorsConfig.find(
          req.user.orgId as string,
          connectorName,
        );

        // Extract received scopes and filter enabled apps
        const receivedScopes = tokenData.scope.split(' ');
        const enabledApps = Object.keys(scopeToAppMap)
          .filter((scope) => receivedScopes.includes(scope))
          .map((scope) => scopeToAppMap[scope])
          .filter((app): app is GoogleWorkspaceApp => app !== undefined);

        // Prepare event for sync service. The producer is the container's shared one, connected at start-up and
        // reconnected by publish itself: it is neither started nor stopped here.
        let event: Event;

        if (connector) {
          // Update existing connector (written after the event is queued)
          event = {
            eventType: EventType.AppEnabledEvent,
            timestamp: Date.now(),
            payload: {
              orgId: req.user.orgId,
              appGroup: connector.name,
              appGroupId: connector._id,
              credentialsRoute: `${config.cmBackend}/${GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH}`,
              refreshTokenRoute: `${config.cmBackend}/${REFRESH_TOKEN_PATH}`,
              apps: enabledApps,
              syncAction: 'immediate',
            } as AppEnabledEvent,
          };

          await publishSyncStart(eventService, event);
          connector =
            (await connectorsConfig.enable(
              req.user.orgId as string,
              connectorName,
              req.user.userId as string,
            )) ?? connector;

          logger.info('Google Workspace connector enabled', {
            connector_config_id: connector._id,
            apps: enabledApps,
          });

          res.status(200).json({
            message: 'Connector is now enabled',
            connector,
          });
        } else {
          // Create new connector
          connector = await connectorsConfig.createEnabled(
            req.user.orgId as string,
            connectorName,
            req.user.userId as string,
          );

          if (!connector) {
            throw new InternalServerError('Error creating connector');
          }

          event = {
            eventType: EventType.AppEnabledEvent,
            timestamp: Date.now(),
            payload: {
              orgId: req.user.orgId,
              appGroup: connector.name,
              appGroupId: connector._id,
              credentialsRoute: `${config.cmBackend}/${GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH}`,
              refreshTokenRoute: `${config.cmBackend}/${REFRESH_TOKEN_PATH}`,
              apps: [
                GoogleWorkspaceApp.Drive,
                GoogleWorkspaceApp.Gmail,
                GoogleWorkspaceApp.Calendar,
              ],
              syncAction: 'immediate',
            } as AppEnabledEvent,
          };

          await publishSyncStart(eventService, event);

          logger.info('Google Workspace connector created and enabled', {
            connector_config_id: connector._id,
          });

          res.status(201).json({
            message: `Connector ${connectorId} created and enabled`,
            connector,
          });
        }
      } catch (error) {
        next(error);
      }
    },
  );

  /**
   * @deprecated Legacy endpoint for refreshing individual connector tokens
   * POST /internal/refreshIndividualConnectorToken
   * Refresh access token using refresh token (internal use only)
   */
  router.post(
    '/internal/refreshIndividualConnectorToken',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        // Get refresh token from storage
        const refreshTokenResponse = await getRefreshTokenCredentials(
          req,
          config.cmBackend,
        );

        if (
          refreshTokenResponse.statusCode !== 200 ||
          !refreshTokenResponse.data.refresh_token
        ) {
          throw failedCall('Error getting refresh token from storage', refreshTokenResponse);
        }

        // Get connector configuration
        const configResponse = await getRefreshTokenConfig(req, config.cmBackend);

        if (configResponse.statusCode !== 200) {
          throw failedCall('Error getting connector config', configResponse);
        }

        const configData = configResponse.data;
        if (!configData.clientId || !configData.clientSecret) {
          throw new InternalServerError(GOOGLE_CLIENT_NOT_CONFIGURED);
        }

        const enableRealTimeUpdates = configData?.enableRealTimeUpdates;
        const topicName = configData?.topicName;

        // Retry logic for token refresh
        let retryCount = 0;
        let tokenExchangeSuccessful = false;
        let tokenData: any;

        while (retryCount < 3 && !tokenExchangeSuccessful) {
          try {
            const { data } = await axios.post(
              GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH,
              {
                refresh_token: refreshTokenResponse.data.refresh_token,
                client_id: configData.clientId,
                client_secret: configData.clientSecret,
                grant_type: 'refresh_token',
              },
            );

            tokenData = data;
            tokenExchangeSuccessful = true;
          } catch (error) {
            // Each attempt already has its upstream.call.end line.
            retryCount++;

            if (retryCount < 3) {
              // Exponential backoff with jitter
              const delayMs =
                Math.pow(2, retryCount) * 1000 + Math.random() * 1000;
              await new Promise((resolve) => setTimeout(resolve, delayMs));
            } else {
              throw error;
            }
          }
        }

        if (!tokenExchangeSuccessful) {
          throw new Error('Failed to exchange token after multiple retries');
        }

        // Store new access token
        const updateResponse = await setRefreshTokenCredentials(
          req,
          config.cmBackend,
          tokenData.access_token,
          refreshTokenResponse.data.refresh_token,
          tokenData.expires_in * 1000 + Date.now(),
          refreshTokenResponse.data?.refresh_token_expiry_time || undefined,
          enableRealTimeUpdates,
          topicName,
        );

        if (updateResponse.statusCode !== 200) {
          throw failedCall('Error updating access token', updateResponse);
        }

        logger.info('Access token refreshed successfully');

        res.status(200).json({
          message: 'Access token updated successfully',
        });
      } catch (error) {
        next(error);
      }
    },
  );

  /**
   * @deprecated Internal endpoint for updating app configuration
   * POST /updateAppConfig
   * Update application configuration (internal use only)
   */
  router.post(
    '/updateAppConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      _req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        config = await loadAppConfig();
        container.rebind<AppConfig>('AppConfig').toDynamicValue(() => config);

        logger.info('Connector configuration updated successfully');

        res.status(200).json({
          message: 'Connectors configuration updated successfully',
        });
      } catch (error) {
        next(error);
      }
    },
  );

  return router;
}
