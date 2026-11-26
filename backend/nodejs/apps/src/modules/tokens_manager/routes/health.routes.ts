import { Request, RequestHandler, Response, Router } from 'express';
import { serviceHealthChecks } from '../../agent-harness/ops/service-health-checks';
import { Container } from 'inversify';
import { readStoreEpoch } from '../../../libs/db/boot';
import { pgRuntime } from '../../../libs/db/uow';
import { MongoService } from '../../../libs/services/mongo.service';
import { defaultRedisMode } from '../../../libs/services/redis/connectionProviderFactory';
import { ICacheService } from '../../../libs/services/cache/cacheService.interface';
import { TokenEventProducer } from '../services/token-event.producer';
import { Logger }  from '../../../libs/services/logger.service';
import { classifyError } from '../../../libs/utils/error-class';
import { safeCause } from '../../../libs/errors/backend-error';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import axios from 'axios';
import { AppConfig } from '../config/config';
import { ConfigService } from '../services/cm.service';
import {
  AgentHarnessHealth,
  agentHarnessHealth,
} from '../../agent-harness/config';
import { BuildInfo, getBuildInfo } from '../../../libs/utils/build-info.utils';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import { buildProxyHeaders } from '../controllers/connector.controllers';
import { configuredLocalStorageReplicaProblem } from '../../storage/utils/replicas';
import { isUserOrgAdmin } from '../../user_management/services/user-admin.service';
import { hardenedProfile } from '../../../libs/utils/security-profile';
import { precisionWaitWarningFromStore } from '../utils/precision-warning';

/** What the health page calls the session cache: PostgreSQL, this process's memory or the Redis server. */
function cacheDisplayName(redisMode: string): string {
  if (redisMode === 'postgres') return 'PostgreSQL cache';
  if (redisMode === 'inprocess') return 'In-process cache';
  if (redisMode === 'cluster') return 'Redis (Cluster)';
  if (redisMode === 'memorydb') return 'Redis (MemoryDB)';
  return 'Redis';
}

const STORAGE_WARNING_TIMEOUT_MS = 2000;

// GET /services: each Python /health answers from memory, so a slow answer
// means that service's event loop is busy, not down. 3s marked busy services
// unhealthy under load. Kept under the 10s docker-compose healthcheck timeout,
// which calls this route; no Helm probe does (they call GET /).
const SERVICE_HEALTH_TIMEOUT_MS = 8000;

// The memory report reads every vector collection's configuration and Qdrant's
// telemetry, which took about 12s on a loaded instance. Not a probe target.
const VECTOR_MEMORY_TIMEOUT_MS = 20000;

// The graph and vector stores are probed through the connector service. When
// that service refuses the connection (it is still starting, or it is down) the
// store was never asked, so its state is unknown rather than unhealthy; the
// connector service itself is reported by GET /services.
const connectorRefused = (error: unknown): boolean =>
  (error as { code?: string } | null)?.code === 'ECONNREFUSED';

/** What a dependency probe's line says about an answer, or about no answer. */
const probeAnswer = (status: number): Record<string, unknown> =>
  status === 200 ? { status } : { status, error_class: classifyError(status) };
const probeFailure = (error: unknown): Record<string, unknown> =>
  connectorRefused(error)
    ? { reason: 'connector_refused' }
    : {
        reason:
          (error as { code?: string } | null)?.code === 'ECONNABORTED'
            ? 'timeout'
            : 'request_failed',
        error_class: classifyError(error),
        error,
      };

const logger = Logger.getInstance({
  service: 'HealthStatus'
});

type CheckState = 'healthy' | 'unhealthy' | 'unknown';

/**
 * One line when a dependency's state changes: WARN when it turns unhealthy or unknown, INFO when it
 * recovers, DEBUG while a probe finds what the last one found. The first probe of a healthy
 * dependency is DEBUG too. Probes run every few seconds, so a line per probe would flood the log.
 */
const stateLogger = () => {
  const last = new Map<string, CheckState>();
  return (
    check: string,
    state: CheckState,
    fields: Record<string, unknown> = {},
  ): void => {
    const before = last.get(check);
    last.set(check, state);
    const unchanged =
      state === before || (before === undefined && state === 'healthy');
    const level = unchanged ? 'debug' : state === 'healthy' ? 'info' : 'warn';
    logger[level]('health.check.changed', {
      check,
      state,
      previous: before ?? 'none',
      ...fields,
    });
  };
};

const TYPES = {
  MongoService: 'MongoService',
  RedisService: 'RedisService',
  TokenEventProducer: 'KafkaService',
  KeyValueStoreService: 'KeyValueStoreService',
};

export interface HealthStatus {
  status: 'healthy' | 'unhealthy';
  timestamp: string;
  services: Record<string, string>;
  serviceNames: Record<string, string>;
  /** Reported, never counted in `status`: the rest of PipesHub serves while the harness is unavailable. */
  agentHarness: AgentHarnessHealth;
  deployment: {
    kvStoreType: string;
    messageBrokerType: string;
    graphDbType: string;
    vectorDbType: string;
    // The cache mode this process runs (defaultRedisMode): REDIS_MODE, else
    // `postgres` when KV_STORE_TYPE and MESSAGE_BROKER are both postgres.
    // Read from the process env, not the KV deployment config.
    redisMode: string;
  };
  build: BuildInfo;
  // Configuration that works but loses data or misbehaves; status stays healthy.
  warnings?: string[];
}

/**
 * What an unauthenticated caller learns (R41OPS-FU-8, ADR-WEB-01 D8): whether
 * the app is healthy and whether durable runs are ready, which probes and lanes
 * wait on. The stores, their modes, the build and the warnings are for an admin.
 */
export interface PublicHealthStatus {
  status: HealthStatus['status'];
  agentHarness: {
    status: AgentHarnessHealth['status'];
    capabilities?: 'settled' | 'settling';
  };
}

export function publicHealth(health: HealthStatus): PublicHealthStatus {
  const harness = health.agentHarness;
  return {
    status: health.status,
    agentHarness:
      harness.status === 'available' && harness.capabilities !== undefined
        ? { status: harness.status, capabilities: harness.capabilities }
        : { status: harness.status },
  };
}

/** The product's own services, by the names every installer and healthcheck reads; nothing a check adds. */
export const PUBLIC_SERVICE_NAMES = [
  'query',
  'connector',
  'indexing',
  'docling',
  'embedding',
  'parsing',
  'extraction',
] as const;

export function publicServicesHealth(body: {
  status: string;
  services: Record<string, string>;
}): { status: string; services: Record<string, string> } {
  const services: Record<string, string> = {};
  for (const name of PUBLIC_SERVICE_NAMES) {
    if (body.services[name] !== undefined) services[name] = body.services[name];
  }
  return { status: body.status, services };
}

const HEALTH_DETAIL = 'healthDetail';

const passes = (
  handler: RequestHandler,
  req: Request,
  res: Response,
): Promise<boolean> =>
  new Promise((resolve) => {
    try {
      const result = handler(req, res, (error?: unknown) => resolve(!error));
      void Promise.resolve(result).catch(() => resolve(false));
    } catch {
      resolve(false);
    }
  });

/**
 * Marks the request for the full report when it carries an org admin's token.
 * Never refuses: a probe sends no token, and a token that does not pass gets
 * the public answer, as a caller with none does. On the compatibility profile
 * (ADR-OPS-07) every caller gets the full report, as a release without
 * ADR-WEB-01 D8 answered, so a monitor that parses it keeps working.
 */
export function healthAudience(
  authMiddleware: AuthMiddleware,
  isAdmin: (userId: string, orgId: string) => Promise<boolean> = isUserOrgAdmin,
  env: NodeJS.ProcessEnv = process.env,
): RequestHandler {
  const authenticate = authMiddleware.authenticate.bind(
    authMiddleware,
  ) as RequestHandler;
  const configRead = requireScopes(
    OAuthScopeNames.CONFIG_READ,
  ) as RequestHandler;
  return async (req, res, next) => {
    if (!hardenedProfile(env)) {
      res.locals[HEALTH_DETAIL] = true;
      next();
      return;
    }
    res.locals[HEALTH_DETAIL] = false;
    if (req.headers.authorization) {
      try {
        if (
          (await passes(authenticate, req, res)) &&
          (await passes(configRead, req, res))
        ) {
          const user = (req as AuthenticatedUserRequest).user;
          const userId: unknown = user?.userId;
          const orgId: unknown = user?.orgId;
          res.locals[HEALTH_DETAIL] =
            Boolean(userId && orgId) &&
            (await isAdmin(String(userId), String(orgId)));
        }
      } catch (error) {
        logger.debug('health detail not granted', {
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
    next();
  };
}

const wantsDetail = (res: Response): boolean => {
  // A hand-built response (an internal caller, a test) may come without locals.
  const response: { locals?: Record<string, unknown> } = res;
  return response.locals?.[HEALTH_DETAIL] === true;
};

export function createHealthRouter(
  container: Container,
  configurationManagerContainer: Container
): Router {
  const router = Router();
  const redis = container.get<ICacheService>(TYPES.RedisService);
  const tokenEventProducer = container.get<TokenEventProducer>(TYPES.TokenEventProducer);
  // Not bound when the document store is PostgreSQL.
  const mongooseService = container.isBound(TYPES.MongoService)
    ? container.get<MongoService>(TYPES.MongoService)
    : null;
  const keyValueStoreService = configurationManagerContainer.get<KeyValueStoreService>(
    TYPES.KeyValueStoreService,
  );

  const appConfig = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  const configService = ConfigService.getInstance();
  // Resolved here, at startup: from source it shells out to git once.
  const build = getBuildInfo();
  const noteCheck = stateLogger();

  async function getDeploymentConfig() {
    try {
      const fresh = await configService.readDeploymentConfig();
      if (fresh && Object.keys(fresh).length > 0) {
        return {
          dataStoreType: fresh.dataStoreType || undefined,
          messageBrokerType: fresh.messageBrokerType || appConfig.deployment.messageBrokerType,
          kvStoreType: fresh.kvStoreType || appConfig.deployment.kvStoreType,
          vectorDbType: fresh.vectorDbType || undefined,
        };
      }
    } catch (error) {
      // Answered from the boot config: degraded, not down.
      logger.warn('Failed to refresh deployment config', {
        error: safeCause(error),
      });
    }
    return {
      dataStoreType: undefined as string | undefined,
      messageBrokerType: appConfig.deployment.messageBrokerType,
      kvStoreType: appConfig.deployment.kvStoreType,
      vectorDbType: undefined as string | undefined,
    };
  }

  const audience = healthAudience(authMiddleware);

  router.get('/', audience, async (_req, res, next) => {
    try {
      const deployment = await getDeploymentConfig();
      const services: Record<string, string> = {
        redis: 'unknown',
        messageBroker: 'unknown',
        mongodb: 'unknown',
        graphDb: 'unknown',
        vectorDb: 'unknown',
      };

      const brokerName =
        deployment.messageBrokerType === 'redis'
          ? 'Redis Streams'
          : deployment.messageBrokerType === 'postgres'
            ? 'PostgreSQL queue'
            : 'Kafka';
      const graphDbName = deployment.dataStoreType === 'arangodb' ? 'ArangoDB' : deployment.dataStoreType === 'postgres' ? 'PostgreSQL' : 'Neo4j';
      const vectorDbNames: Record<string, string> = {
        qdrant: 'Qdrant',
        opensearch: 'OpenSearch',
        redis: 'Redis',
        pgvector: 'pgvector',
      };
      const vectorDbName = vectorDbNames[deployment.vectorDbType || ''] || 'VectorDB';

      // One value for the row name and the published mode the page draws its logo from.
      const redisMode = defaultRedisMode().toLowerCase();
      const redisDisplayName = cacheDisplayName(redisMode);

      const serviceNames: Record<string, string> = {
        redis: redisDisplayName,
        messageBroker: brokerName,
        mongodb: 'MongoDB',
        graphDb: graphDbName,
        vectorDb: vectorDbName,
      };

      // When KV store uses etcd, add it as a separate service
      if (deployment.kvStoreType === 'etcd') {
        services.KVStoreservice = 'unknown';
        serviceNames.KVStoreservice = 'etcd';
      }

      let overallHealthy = true;

      try {
        await redis.get('health-check');
        services.redis = 'healthy';
      } catch (error) {
        services.redis = 'unhealthy';
        overallHealthy = false;
      }

      try {
        await tokenEventProducer.healthCheck();
        services.messageBroker = 'healthy';
      } catch (error) {
        services.messageBroker = 'unhealthy';
        overallHealthy = false;
      }

      if (mongooseService) {
        try {
          const isMongoHealthy = await mongooseService.healthCheck();
          services.mongodb = isMongoHealthy ? 'healthy' : 'unhealthy';
          if (!isMongoHealthy) overallHealthy = false;
        } catch (error) {
          services.mongodb = 'unhealthy';
          overallHealthy = false;
        }
      } else {
        delete services.mongodb;
        delete serviceNames.mongodb;
        // The document store is PostgreSQL: report it in the place MongoDB would have been.
        try {
          await readStoreEpoch(pgRuntime());
          services.postgres = 'healthy';
        } catch {
          services.postgres = 'unhealthy';
          overallHealthy = false;
        }
        serviceNames.postgres = 'PostgreSQL';
      }

      // KV Store — only check separately when using etcd
      if (deployment.kvStoreType === 'etcd') {
        try {
          const isKVServiceHealthy = await keyValueStoreService.healthCheck();
          services.KVStoreservice = isKVServiceHealthy ? 'healthy' : 'unhealthy';
          if (!isKVServiceHealthy) overallHealthy = false;
        } catch (exception) {
          services.KVStoreservice = 'unhealthy';
          overallHealthy = false;
        }
      }

      // Graph DB — check the one actually deployed
      if (!deployment.dataStoreType) {
        // Python backend hasn't written dataStoreType to KV store yet
        services.graphDb = 'pending';
        logger.debug('dataStoreType not yet available in deployment config — Python backend may not have started');
      } else if (
        deployment.dataStoreType === 'neo4j' ||
        deployment.dataStoreType === 'arangodb' ||
        deployment.dataStoreType === 'postgres'
      ) {
        try {
          // Delegate to the Python connector service which probes the graph DB
          // using the same driver the application uses (Bolt for Neo4j,
          // python-arango for ArangoDB, the graph provider for PostgreSQL). This avoids the Node.js layer having
          // to manage DB credentials or knowing which HTTP ports to probe —
          // both of which break for managed cloud deployments (e.g. Neo4j Aura
          // doesn't expose the HTTP discovery ports 7474/7473).
          // Stay at 5s. Helm startup, readiness, and liveness probes call this
          // route with timeoutSeconds 5; a longer wait here fails the probe
          // and restarts the pod. The connector probe returns before this.
          const graphDbResp = await axios.get(
            `${appConfig.connectorBackend}/health/graph-db`,
            { timeout: 5000, validateStatus: () => true },
          );
          const state: CheckState = graphDbResp.status === 200 ? 'healthy' : 'unhealthy';
          services.graphDb = state;
          if (state !== 'healthy') overallHealthy = false;
          noteCheck('graph_db', state, probeAnswer(graphDbResp.status));
        } catch (error: unknown) {
          overallHealthy = false;
          const state: CheckState = connectorRefused(error) ? 'unknown' : 'unhealthy';
          services.graphDb = state;
          noteCheck('graph_db', state, probeFailure(error));
        }
      }

      // Vector DB — delegate to the Python connector service which knows the
      // configured provider (Qdrant, OpenSearch, or Redis) via VECTOR_DB_TYPE.
      if (!deployment.vectorDbType) {
        services.vectorDb = 'pending';
        logger.debug('vectorDbType not yet available in deployment config — Python backend may not have started');
      } else {
        try {
          const vectorDbResp = await axios.get(
            `${appConfig.connectorBackend}/health/vector-db`,
            { timeout: 5000, validateStatus: () => true },
          );
          const state: CheckState = vectorDbResp.status === 200 ? 'healthy' : 'unhealthy';
          services.vectorDb = state;
          if (state !== 'healthy') overallHealthy = false;
          noteCheck('vector_db', state, probeAnswer(vectorDbResp.status));
        } catch (error) {
          const state: CheckState = connectorRefused(error) ? 'unknown' : 'unhealthy';
          services.vectorDb = state;
          overallHealthy = false;
          noteCheck('vector_db', state, probeFailure(error));
        }
      }

      // Warnings only: a stalled store must not hold the health answer.
      const warningWithin = async (
        read: Promise<string | null>,
        what: string,
      ): Promise<string | null> => {
        let timer: NodeJS.Timeout | undefined;
        try {
          return await Promise.race([
            read,
            new Promise<null>((resolve) => {
              timer = setTimeout(() => {
                resolve(null);
              }, STORAGE_WARNING_TIMEOUT_MS);
            }),
          ]);
        } catch (error) {
          logger.warn('Could not read a health warning source', {
            source: what,
            error,
          });
          return null;
        } finally {
          clearTimeout(timer);
        }
      };
      const warnings = (
        await Promise.all([
          warningWithin(
            configuredLocalStorageReplicaProblem(keyValueStoreService),
            'the storage configuration',
          ),
          warningWithin(
            precisionWaitWarningFromStore(keyValueStoreService),
            'the weight-precision state',
          ),
        ])
      ).filter((warning): warning is string => warning !== null);

      const health: HealthStatus = {
        status: overallHealthy ? 'healthy' : 'unhealthy',
        timestamp: new Date().toISOString(),
        services,
        serviceNames,
        agentHarness: agentHarnessHealth(),
        deployment: {
          kvStoreType: deployment.kvStoreType,
          messageBrokerType: deployment.messageBrokerType,
          graphDbType: deployment.dataStoreType || 'pending',
          vectorDbType: deployment.vectorDbType || 'pending',
          redisMode,
        },
        build,
        ...(warnings.length > 0 ? { warnings } : {}),
      };

      res.status(200).json(wantsDetail(res) ? health : publicHealth(health));
    } catch (exception: unknown) {
      // The error middleware logs it once and answers 500.
      next(exception);
    }
  });

  // Combined services health check (Python query + connector + indexing + docling + embedding,
  // plus parsing + extraction when USE_PARSING_SERVICE=true)
  router.get('/services', audience, async (_req, res, _next) => {
    try {
      const aiHealthUrl = `${appConfig.aiBackend}/health`;
      const connectorHealthUrl = `${appConfig.connectorBackend}/health`;
      const indexingHealthUrl = `${appConfig.indexingBackend}/health`;
      const doclingBackend = process.env.DOCLING_BACKEND || 'http://localhost:8081';
      const doclingHealthUrl = `${doclingBackend}/health`;
      const embeddingBackend = (process.env.EMBEDDING_SERVER_URL || 'http://localhost:8002').replace(/\/v1\/?$/, '');
      const embeddingHealthUrl = `${embeddingBackend}/health`;
      const parsingServicesEnabled = (process.env.USE_PARSING_SERVICE || '').toLowerCase() === 'true';
      const parsingBackend = process.env.PARSING_SERVICE_URL || 'http://localhost:8092';
      const extractionBackend = process.env.EXTRACTION_SERVICE_URL || 'http://localhost:8093';

      const probe = { timeout: SERVICE_HEALTH_TIMEOUT_MS };
      const baseSettled = Promise.allSettled([
        axios.get(aiHealthUrl, probe),
        axios.get(connectorHealthUrl, probe),
        axios.get(indexingHealthUrl, probe),
        axios.get(doclingHealthUrl, probe),
        axios.get(embeddingHealthUrl, probe),
      ]);
      const parsingSettled = parsingServicesEnabled
        ? Promise.allSettled([
            axios.get(`${parsingBackend}/health`, probe),
            axios.get(`${extractionBackend}/health`, probe),
          ])
        : null;

      const [aiResp, connectorResp, indexingResp, doclingResp, embeddingResp] = await baseSettled;

      const isServiceHealthy = (res: PromiseSettledResult<any>) =>
        res.status === 'fulfilled' &&
        res.value.status === 200 &&
        res.value.data?.status === 'healthy';

      const aiOk = isServiceHealthy(aiResp);
      const connectorOk = isServiceHealthy(connectorResp);
      const indexingOk = isServiceHealthy(indexingResp);
      const doclingOk = isServiceHealthy(doclingResp);
      const embeddingOk = isServiceHealthy(embeddingResp);

      const extra = serviceHealthChecks().map((check) => ({
        check,
        view: check.view(),
      }));
      // Critical services: query + connector (required for core functionality), and any critical added check
      const overallHealthy =
        aiOk &&
        connectorOk &&
        extra.every(
          ({ check, view }) => !check.critical || view.status === 'ok',
        );

      const services: Record<string, string> = {
        query: aiOk ? 'healthy' : 'unhealthy',
        connector: connectorOk ? 'healthy' : 'unhealthy',
        indexing: indexingOk ? 'healthy' : 'unhealthy',
        docling: doclingOk ? 'healthy' : 'unhealthy',
        embedding: embeddingOk ? 'healthy' : 'unhealthy',
      };
      if (parsingSettled) {
        const [parsingResp, extractionResp] = await parsingSettled;
        services.parsing = isServiceHealthy(parsingResp) ? 'healthy' : 'unhealthy';
        services.extraction = isServiceHealthy(extractionResp) ? 'healthy' : 'unhealthy';
      }

      const details: Record<string, string> = {};
      for (const { check, view } of extra) {
        services[check.name] = view.status === 'ok' ? 'healthy' : 'unhealthy';
        if (view.detail !== undefined) details[check.name] = view.detail;
      }
      const report = {
        status: overallHealthy ? 'healthy' : 'unhealthy',
        timestamp: new Date().toISOString(),
        services,
        ...(Object.keys(details).length > 0 ? { details } : {}),
      };
      res
        .status(200)
        .json(wantsDetail(res) ? report : publicServicesHealth(report));
    } catch (error: unknown) {
      logger.error('Combined services health check failed', { error });
      const services: Record<string, string> = {
        query: 'unknown',
        connector: 'unknown',
        indexing: 'unknown',
        docling: 'unknown',
        embedding: 'unknown',
      };
      if ((process.env.USE_PARSING_SERVICE || '').toLowerCase() === 'true') {
        services.parsing = 'unknown';
        services.extraction = 'unknown';
      }
      const report = {
        status: 'unhealthy',
        timestamp: new Date().toISOString(),
        services,
      };
      res
        .status(200)
        .json(wantsDetail(res) ? report : publicServicesHealth(report));
    }
  });

  // Whether the vector database has the memory to keep search fast. Kept off
  // GET / because that route is the Helm probe target (5s timeout) and this one
  // reads every collection's configuration. Admin-only: it reports index sizes.
  // Advisory: it never changes any service's status.
  router.get(
    '/vector-db/memory',
    authMiddleware.authenticate.bind(authMiddleware),
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    async (req: AuthenticatedUserRequest, res) => {
      try {
        const resp = await axios.get(
          `${appConfig.connectorBackend}/health/vector-db/memory`,
          {
            headers: buildProxyHeaders(req),
            timeout: VECTOR_MEMORY_TIMEOUT_MS,
            validateStatus: () => true,
          },
        );
        const data: unknown = resp.data;
        if (resp.status === 200 && typeof data === 'object' && data !== null) {
          res.status(200).json(data);
          return;
        }
        logger.warn('vector-db memory report returned non-200', {
          status: resp.status,
        });
      } catch (error: unknown) {
        logger.warn('vector-db memory report failed', { error });
      }
      res.status(502).json({ status: 'unknown' });
    },
  );

  return router;
}
