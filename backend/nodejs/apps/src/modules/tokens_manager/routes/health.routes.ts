import { Router } from 'express';
import { Container } from 'inversify';
import { MongoService } from '../../../libs/services/mongo.service';
import { RedisService } from '../../../libs/services/redis.service';
import { TokenEventProducer } from '../services/token-event.producer';
import { Logger }  from '../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import axios from 'axios';
import { AppConfig } from '../config/config';
import { ConfigService } from '../services/cm.service';

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
  details?: Record<string, ServiceHealthDetail>;
  serviceNames: Record<string, string>;
  deployment: {
    kvStoreType: string;
    messageBrokerType: string;
    graphDbType: string;
    vectorDbType: string;
  };
}

interface ServiceHealthDetail {
  status: 'healthy' | 'unhealthy' | 'starting' | 'pending' | 'unknown';
  message: string;
  endpoint?: string;
  latencyMs?: number;
}

export function createHealthRouter(
  container: Container,
  configurationManagerContainer: Container
): Router {
  const router = Router();
  const redis = container.get<RedisService>(TYPES.RedisService);
  const tokenEventProducer = container.get<TokenEventProducer>(TYPES.TokenEventProducer);
  const mongooseService = container.get<MongoService>(TYPES.MongoService);
  const keyValueStoreService = configurationManagerContainer.get<KeyValueStoreService>(
    TYPES.KeyValueStoreService,
  );

  const appConfig = container.get<AppConfig>('AppConfig');
  const configService = ConfigService.getInstance();

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

  router.get('/', async (_req, res, next) => {
    try {
      const deployment = await getDeploymentConfig();
      const services: Record<string, string> = {
        redis: 'unknown',
        messageBroker: 'unknown',
        mongodb: 'unknown',
        graphDb: 'unknown',
        vectorDb: 'unknown',
      };

      const brokerName = deployment.messageBrokerType === 'redis' ? 'Redis Streams' : 'Kafka';
      const graphDbName = deployment.dataStoreType === 'arangodb' ? 'ArangoDB' : 'Neo4j';
      const vectorDbNames: Record<string, string> = {
        qdrant: 'Qdrant',
        opensearch: 'OpenSearch',
        redis: 'Redis',
      };
      const vectorDbName = vectorDbNames[deployment.vectorDbType || ''] || 'VectorDB';

      const serviceNames: Record<string, string> = {
        redis: 'Redis',
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
      const details: Record<string, ServiceHealthDetail> = {};

      try {
        await redis.get('health-check');
        services.redis = 'healthy';
        details.redis = { status: 'healthy', message: 'Redis responded to ping' };
      } catch (error) {
        services.redis = 'unhealthy';
        details.redis = { status: 'starting', message: 'Waiting for Redis connection' };
        overallHealthy = false;
      }

      try {
        await tokenEventProducer.healthCheck();
        services.messageBroker = 'healthy';
        details.messageBroker = { status: 'healthy', message: `${brokerName} is reachable` };
      } catch (error) {
        services.messageBroker = 'unhealthy';
        details.messageBroker = { status: 'starting', message: `Waiting for ${brokerName}` };
        overallHealthy = false;
      }

      try {
        const isMongoHealthy = await mongooseService.healthCheck();
        services.mongodb = isMongoHealthy ? 'healthy' : 'unhealthy';
        details.mongodb = {
          status: isMongoHealthy ? 'healthy' : 'starting',
          message: isMongoHealthy ? 'MongoDB connection is ready' : 'Waiting for MongoDB connection',
        };
        if (!isMongoHealthy) overallHealthy = false;
      } catch (error) {
        services.mongodb = 'unhealthy';
        details.mongodb = { status: 'starting', message: 'Waiting for MongoDB connection' };
        overallHealthy = false;
      }

      // KV Store — only check separately when using etcd
      if (deployment.kvStoreType === 'etcd') {
        try {
          const isKVServiceHealthy = await keyValueStoreService.healthCheck();
          services.KVStoreservice = isKVServiceHealthy ? 'healthy' : 'unhealthy';
          details.KVStoreservice = {
            status: isKVServiceHealthy ? 'healthy' : 'starting',
            message: isKVServiceHealthy ? 'etcd is reachable' : 'Waiting for etcd',
          };
          if (!isKVServiceHealthy) overallHealthy = false;
        } catch (exception) {
          services.KVStoreservice = 'unhealthy';
          details.KVStoreservice = { status: 'starting', message: 'Waiting for etcd' };
          overallHealthy = false;
        }
      }

      // Graph DB — check the one actually deployed
      if (!deployment.dataStoreType) {
        // Python backend hasn't written dataStoreType to KV store yet
        services.graphDb = 'pending';
        details.graphDb = {
          status: 'pending',
          message: 'Waiting for backend deployment configuration',
        };
        logger.info('dataStoreType not yet available in deployment config — Python backend may not have started');
      } else if (deployment.dataStoreType === 'neo4j' || deployment.dataStoreType === 'arangodb') {
        const endpoint = `${appConfig.connectorBackend}/health/graph-db`;
        const startedAt = Date.now();
        try {
          // Delegate to the Python connector service which probes the graph DB
          // using the same driver the application uses (Bolt for Neo4j,
          // python-arango for ArangoDB). This avoids the Node.js layer having
          // to manage DB credentials or knowing which HTTP ports to probe —
          // both of which break for managed cloud deployments (e.g. Neo4j Aura
          // doesn't expose the HTTP discovery ports 7474/7473).
          const graphDbResp = await axios.get(
            endpoint,
            { timeout: 5000, validateStatus: () => true },
          );
          services.graphDb = graphDbResp.status === 200 ? 'healthy' : 'unhealthy';
          details.graphDb = {
            status: graphDbResp.status === 200 ? 'healthy' : 'unhealthy',
            message: graphDbResp.status === 200 ? `${graphDbName} is reachable` : `${graphDbName} responded with HTTP ${graphDbResp.status}`,
            endpoint,
            latencyMs: Date.now() - startedAt,
          };
          if (graphDbResp.status !== 200) overallHealthy = false;
        } catch (error) {
          services.graphDb = 'unhealthy';
          details.graphDb = {
            status: 'starting',
            message: `Waiting for ${graphDbName}`,
            endpoint,
            latencyMs: Date.now() - startedAt,
          };
          overallHealthy = false;
        }
      }

      // Vector DB — delegate to the Python connector service which knows the
      // configured provider (Qdrant, OpenSearch, or Redis) via VECTOR_DB_TYPE.
      if (!deployment.vectorDbType) {
        services.vectorDb = 'pending';
        details.vectorDb = {
          status: 'pending',
          message: 'Waiting for vector DB configuration',
        };
        logger.info('vectorDbType not yet available in deployment config — Python backend may not have started');
      } else {
        const vectorDbEndpoint = `${appConfig.connectorBackend}/health/vector-db`;
        const vectorDbStartedAt = Date.now();
        try {
          const vectorDbResp = await axios.get(vectorDbEndpoint, {
            timeout: 5000,
            validateStatus: () => true,
          });
          const ok = vectorDbResp.status === 200;
          services.vectorDb = ok ? 'healthy' : 'unhealthy';
          details.vectorDb = {
            status: ok ? 'healthy' : 'unhealthy',
            message: ok
              ? `${vectorDbName} is reachable`
              : `${vectorDbName} responded with HTTP ${vectorDbResp.status}`,
            endpoint: vectorDbEndpoint,
            latencyMs: Date.now() - vectorDbStartedAt,
          };
          if (!ok) overallHealthy = false;
        } catch (error) {
          services.vectorDb = 'unhealthy';
          details.vectorDb = {
            status: 'starting',
            message: `Waiting for ${vectorDbName}`,
            endpoint: vectorDbEndpoint,
            latencyMs: Date.now() - vectorDbStartedAt,
          };
          overallHealthy = false;
        }
      }

      const health: HealthStatus = {
        status: overallHealthy ? 'healthy' : 'unhealthy',
        timestamp: new Date().toISOString(),
        services,
        details,
        serviceNames,
        deployment: {
          kvStoreType: deployment.kvStoreType,
          messageBrokerType: deployment.messageBrokerType,
          graphDbType: deployment.dataStoreType || 'pending',
          vectorDbType: deployment.vectorDbType || 'pending',
        },
      };

      res.status(200).json(health);
    } catch (exception: any) {
      logger.error("health check status failed", exception.message);
      next()
    }
  });

  // Combined services health check (Python query + connector + indexing + docling + embedding services)
  router.get('/services', async (_req, res, _next) => {
    try {
      const aiHealthUrl = `${appConfig.aiBackend}/health`;
      const connectorHealthUrl = `${appConfig.connectorBackend}/health`;
      const indexingHealthUrl = `${appConfig.indexingBackend}/health`;
      const doclingBackend = process.env.DOCLING_BACKEND || 'http://localhost:8081';
      const doclingHealthUrl = `${doclingBackend}/health`;
      const embeddingBackend = (process.env.EMBEDDING_SERVER_URL || 'http://localhost:8002').replace(/\/v1\/?$/, '');
      const embeddingHealthUrl = `${embeddingBackend}/health`;

      const checkHttpService = async (endpoint: string, label: string): Promise<{ ok: boolean; detail: ServiceHealthDetail }> => {
        const startedAt = Date.now();
        try {
          const response = await axios.get(endpoint, { timeout: 3000, validateStatus: () => true });
          const ok = response.status === 200 && response.data?.status === 'healthy';
          return {
            ok,
            detail: {
              status: ok ? 'healthy' : 'unhealthy',
              message: ok ? `${label} is ready` : `${label} responded but is not healthy`,
              endpoint,
              latencyMs: Date.now() - startedAt,
            },
          };
        } catch {
          return {
            ok: false,
            detail: {
              status: 'starting',
              message: `Waiting for ${label}`,
              endpoint,
              latencyMs: Date.now() - startedAt,
            },
          };
        }
      };

      const [aiResp, connectorResp, indexingResp, doclingResp, embeddingResp] = await Promise.all([
        checkHttpService(aiHealthUrl, 'Query Service'),
        checkHttpService(connectorHealthUrl, 'Connector Service'),
        checkHttpService(indexingHealthUrl, 'Indexing Service'),
        checkHttpService(doclingHealthUrl, 'Docling Service'),
        checkHttpService(embeddingHealthUrl, 'Embedding Service'),
      ]);

      const aiOk = aiResp.ok;
      const connectorOk = connectorResp.ok;
      const indexingOk = indexingResp.ok;
      const doclingOk = doclingResp.ok;
      const embeddingOk = embeddingResp.ok;

      // Critical services: query + connector (required for core functionality)
      const overallHealthy = aiOk && connectorOk;

      res.status(200).json({
        status: overallHealthy ? 'healthy' : 'unhealthy',
        timestamp: new Date().toISOString(),
        services: {
          query: aiOk ? 'healthy' : 'unhealthy',
          connector: connectorOk ? 'healthy' : 'unhealthy',
          indexing: indexingOk ? 'healthy' : 'unhealthy',
          docling: doclingOk ? 'healthy' : 'unhealthy',
          embedding: embeddingOk ? 'healthy' : 'unhealthy',
        },
        details: {
          query: aiResp.detail,
          connector: connectorResp.detail,
          indexing: indexingResp.detail,
          docling: doclingResp.detail,
          embedding: embeddingResp.detail,
        },
      });
    } catch (error: any) {
      logger.error('Combined services health check failed', error?.message ?? error);
      res.status(200).json({
        status: 'unhealthy',
        timestamp: new Date().toISOString(),
        services: {
          query: 'unknown',
          connector: 'unknown',
          indexing: 'unknown',
          docling: 'unknown',
          embedding: 'unknown',
        },
      });
    }
  });

  return router;
}
