import { Router } from 'express';
import { Container } from 'inversify';
import { MongoService } from '../../../libs/services/mongo.service';
import { ICacheService } from '../../../libs/services/cache/cacheService.interface';
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
  serviceNames: Record<string, string>;
  deployment: {
    kvStoreType: string;
    messageBrokerType: string;
    graphDbType: string;
    vectorDbType: string;
    // Not written to the KV deployment config -- REDIS_MODE is a
    // process-env setting (R21/R22), so it is read directly rather than
    // via getDeploymentConfig()'s KV-store fresh/fallback path.
    redisMode: string;
  };
}

export function createHealthRouter(
  container: Container,
  configurationManagerContainer: Container
): Router {
  const router = Router();
  const redis = container.get<ICacheService>(TYPES.RedisService);
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

      try {
        const isMongoHealthy = await mongooseService.healthCheck();
        services.mongodb = isMongoHealthy ? 'healthy' : 'unhealthy';
        if (!isMongoHealthy) overallHealthy = false;
      } catch (error) {
        services.mongodb = 'unhealthy';
        overallHealthy = false;
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
      } else if (deployment.dataStoreType === 'neo4j' || deployment.dataStoreType === 'arangodb') {
        try {
          // Delegate to the Python connector service which probes the graph DB
          // using the same driver the application uses (Bolt for Neo4j,
          // python-arango for ArangoDB). This avoids the Node.js layer having
          // to manage DB credentials or knowing which HTTP ports to probe —
          // both of which break for managed cloud deployments (e.g. Neo4j Aura
          // doesn't expose the HTTP discovery ports 7474/7473).
          const graphDbResp = await axios.get(
            `${appConfig.connectorBackend}/health/graph-db`,
            { timeout: 5000, validateStatus: () => true },
          );
          services.graphDb = graphDbResp.status === 200 ? 'healthy' : 'unhealthy';
          if (graphDbResp.status !== 200) overallHealthy = false;
        } catch (error) {
          services.graphDb = 'unhealthy';
          overallHealthy = false;
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
          services.vectorDb = 'unhealthy';
          overallHealthy = false;
        }
      }

      const health: HealthStatus = {
        status: overallHealthy ? 'healthy' : 'unhealthy',
        timestamp: new Date().toISOString(),
        services,
        serviceNames,
        deployment: {
          kvStoreType: deployment.kvStoreType,
          messageBrokerType: deployment.messageBrokerType,
          graphDbType: deployment.dataStoreType || 'pending',
          vectorDbType: deployment.vectorDbType || 'pending',
          redisMode: process.env.REDIS_MODE || 'standalone',
        },
      };

      res.status(200).json(health);
    } catch (exception: any) {
      logger.error("health check status failed", exception.message);
      next()
    }
  });

  // Combined services health check (Python query + connector + indexing + docling + embedding,
  // plus parsing + extraction when USE_PARSING_SERVICE=true)
  router.get('/services', async (_req, res, _next) => {
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

      // Critical services: query + connector (required for core functionality)
      const overallHealthy = aiOk && connectorOk;

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

      res.status(200).json({
        status: overallHealthy ? 'healthy' : 'unhealthy',
        timestamp: new Date().toISOString(),
        services,
      });
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
      res.status(200).json({
        status: 'unhealthy',
        timestamp: new Date().toISOString(),
        services,
      });
    }
  });

  return router;
}
