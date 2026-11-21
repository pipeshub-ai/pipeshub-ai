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

/** What the health page calls the session cache: PostgreSQL, this process's memory or the Redis server. */
function cacheDisplayName(redisMode: string): string {
  if (redisMode === 'postgres') return 'PostgreSQL cache';
  if (redisMode === 'inprocess') return 'In-process cache';
  if (redisMode === 'cluster') return 'Redis (Cluster)';
  if (redisMode === 'memorydb') return 'Redis (MemoryDB)';
  return 'Redis';
}

const STORAGE_WARNING_TIMEOUT_MS = 2000;

// The graph and vector stores are probed through the connector service. When
// that service refuses the connection (it is still starting, or it is down) the
// store was never asked, so its state is unknown rather than unhealthy; the
// connector service itself is reported by GET /services.
const connectorRefused = (error: unknown): boolean =>
  (error as { code?: string } | null)?.code === 'ECONNREFUSED';

const logger = Logger.getInstance({
  service: 'HealthStatus'
});

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
      logger.error('Failed to refresh deployment config', error);
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
        logger.info('dataStoreType not yet available in deployment config — Python backend may not have started');
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
          services.graphDb = graphDbResp.status === 200 ? 'healthy' : 'unhealthy';
          if (graphDbResp.status !== 200) {
            overallHealthy = false;
            Logger.getInstance({ service: 'HealthStatus' }).error(
              'graph-db health check returned non-200',
              { status: graphDbResp.status },
            );
          }
        } catch (error: unknown) {
          overallHealthy = false;
          if (connectorRefused(error)) {
            services.graphDb = 'unknown';
            logger.warn(
              'graph-db not checked: the connector service is not accepting connections',
            );
          } else {
            services.graphDb = 'unhealthy';
            const axiosErr = error as { code?: string; message?: string };
            const timedOut = axiosErr?.code === 'ECONNABORTED';
            Logger.getInstance({ service: 'HealthStatus' }).error(
              timedOut
                ? 'graph-db health check timed out'
                : 'graph-db health check failed',
              axiosErr?.message ?? error,
            );
          }
        }
      }

      // Vector DB — delegate to the Python connector service which knows the
      // configured provider (Qdrant, OpenSearch, or Redis) via VECTOR_DB_TYPE.
      if (!deployment.vectorDbType) {
        services.vectorDb = 'pending';
        logger.info('vectorDbType not yet available in deployment config — Python backend may not have started');
      } else {
        try {
          const vectorDbResp = await axios.get(
            `${appConfig.connectorBackend}/health/vector-db`,
            { timeout: 5000, validateStatus: () => true },
          );
          services.vectorDb = vectorDbResp.status === 200 ? 'healthy' : 'unhealthy';
          if (vectorDbResp.status !== 200) overallHealthy = false;
        } catch (error) {
          services.vectorDb = connectorRefused(error) ? 'unknown' : 'unhealthy';
          overallHealthy = false;
        }
      }

      const warnings: string[] = [];
      // Warning only: a stalled store must not hold the health answer.
      let timer: NodeJS.Timeout | undefined;
      try {
        const replicaProblem = await Promise.race([
          configuredLocalStorageReplicaProblem(keyValueStoreService),
          new Promise<null>((resolve) => {
            timer = setTimeout(() => resolve(null), STORAGE_WARNING_TIMEOUT_MS);
          }),
        ]);
        if (replicaProblem) warnings.push(replicaProblem);
      } catch (error) {
        logger.warn(
          'Could not read the storage configuration for the health warnings',
          error,
        );
      }
      clearTimeout(timer);

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
    } catch (exception: any) {
      logger.error("health check status failed", exception.message);
      next()
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

      const baseSettled = Promise.allSettled([
        axios.get(aiHealthUrl, { timeout: 3000 }),
        axios.get(connectorHealthUrl, { timeout: 3000 }),
        axios.get(indexingHealthUrl, { timeout: 3000 }),
        axios.get(doclingHealthUrl, { timeout: 3000 }),
        axios.get(embeddingHealthUrl, { timeout: 3000 }),
      ]);
      const parsingSettled = parsingServicesEnabled
        ? Promise.allSettled([
            axios.get(`${parsingBackend}/health`, { timeout: 3000 }),
            axios.get(`${extractionBackend}/health`, { timeout: 3000 }),
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
    } catch (error: any) {
      logger.error('Combined services health check failed', error?.message ?? error);
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
            timeout: 5000,
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
        logger.warn(
          'vector-db memory report failed',
          error instanceof Error ? error.message : String(error),
        );
      }
      res.status(502).json({ status: 'unknown' });
    },
  );

  return router;
}
