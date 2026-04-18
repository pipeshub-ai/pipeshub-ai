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
  serviceNames: Record<string, string>;
  deployment: {
    kvStoreType: string;
    messageBrokerType: string;
    graphDbType: string;
  };
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
  const defaultDeployment = { ...appConfig.deployment };
  const configService = ConfigService.getInstance();

  async function getDeploymentConfig() {
    try {
      const fresh = await configService.readDeploymentConfig();
      if (fresh && Object.keys(fresh).length > 0) {
        return {
          dataStoreType: fresh.dataStoreType || defaultDeployment.dataStoreType,
          messageBrokerType: fresh.messageBrokerType || defaultDeployment.messageBrokerType,
          kvStoreType: fresh.kvStoreType || defaultDeployment.kvStoreType,
          vectorDbType: fresh.vectorDbType || defaultDeployment.vectorDbType,
        };
      }
    } catch (error) {
      logger.error('Failed to refresh deployment config', error);
    }
    return defaultDeployment;
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
      const graphDbName = deployment.dataStoreType === 'neo4j' ? 'Neo4j' : 'ArangoDB';

      const serviceNames: Record<string, string> = {
        redis: 'Redis',
        messageBroker: brokerName,
        mongodb: 'MongoDB',
        graphDb: graphDbName,
        vectorDb: 'Qdrant',
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
      if (deployment.dataStoreType === 'neo4j') {
        try {
          const neo4jUri = process.env.NEO4J_URI || 'bolt://localhost:7687';
          const neo4jHttpUrl = neo4jUri
            .replace('bolt://', 'http://')
            .replace('neo4j://', 'http://')
            .replace(':7687', ':7474');
          const neo4jResp = await axios.get(`${neo4jHttpUrl}`, { timeout: 3000 });
          services.graphDb = neo4jResp.status === 200 ? 'healthy' : 'unhealthy';
          if (neo4jResp.status !== 200) overallHealthy = false;
        } catch (error) {
          services.graphDb = 'unhealthy';
          overallHealthy = false;
        }
      } else {
        try {
          const arangoResp = await axios.get(`${appConfig.arango.url}/_api/version`, { timeout: 3000 });
          services.graphDb = arangoResp.status === 200 ? 'healthy' : 'unhealthy';
          if (arangoResp.status !== 200) overallHealthy = false;
        } catch (error) {
          services.graphDb = 'unhealthy';
          overallHealthy = false;
        }
      }

      try {
        const qdrantUrl = `http://${appConfig.qdrant.host}:${appConfig.qdrant.port}`;
        const qdrantResp = await axios.get(`${qdrantUrl}/healthz`, { timeout: 3000 });
        services.vectorDb = qdrantResp.status === 200 ? 'healthy' : 'unhealthy';
        if (qdrantResp.status !== 200) overallHealthy = false;
      } catch (error) {
        services.vectorDb = 'unhealthy';
        overallHealthy = false;
      }

      const health: HealthStatus = {
        status: overallHealthy ? 'healthy' : 'unhealthy',
        timestamp: new Date().toISOString(),
        services,
        serviceNames,
        deployment: {
          kvStoreType: deployment.kvStoreType,
          messageBrokerType: deployment.messageBrokerType,
          graphDbType: deployment.dataStoreType,
        },
      };

      res.status(200).json(health);
    } catch (exception: any) {
      logger.error("health check status failed", exception.message);
      next()
    }
  });

  // Combined services health check (Python query + connector + indexing + docling services)
  router.get('/services', async (_req, res, _next) => {
    try {
      const aiHealthUrl = `${appConfig.aiBackend}/health`;
      const connectorHealthUrl = `${appConfig.connectorBackend}/health`;
      const indexingHealthUrl = `${appConfig.indexingBackend}/health`;
      const doclingBackend = process.env.DOCLING_BACKEND || 'http://localhost:8081';
      const doclingHealthUrl = `${doclingBackend}/health`;

      const [aiResp, connectorResp, indexingResp, doclingResp] = await Promise.allSettled([
        axios.get(aiHealthUrl, { timeout: 3000 }),
        axios.get(connectorHealthUrl, { timeout: 3000 }),
        axios.get(indexingHealthUrl, { timeout: 3000 }),
        axios.get(doclingHealthUrl, { timeout: 3000 }),
      ]);

      const isServiceHealthy = (res: PromiseSettledResult<any>) =>
        res.status === 'fulfilled' &&
        res.value.status === 200 &&
        res.value.data?.status === 'healthy';

      const aiOk = isServiceHealthy(aiResp);
      const connectorOk = isServiceHealthy(connectorResp);
      const indexingOk = isServiceHealthy(indexingResp);
      const doclingOk = isServiceHealthy(doclingResp);

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
        },
      });
    }
  });

  return router;
}
