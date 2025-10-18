import { Router } from 'express';
import { Container } from 'inversify';
import { MongoService } from '../../../libs/services/mongo.service';
import { RedisService } from '../../../libs/services/redis.service';
import { TokenEventProducer } from '../services/token-event.producer';
import { ArangoService } from '../../../libs/services/arango.service';
import { Logger }  from '../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import axios from 'axios';
import { AppConfig } from '../config/config';

const logger = Logger.getInstance({
  service: 'HealthStatus'
});

const TYPES = {
  MongoService: 'MongoService',
  RedisService: 'RedisService',
  TokenEventProducer: 'KafkaService',
  ArangoService: 'ArangoService',
  KeyValueStoreService: 'KeyValueStoreService',
};

export interface HealthStatus {
  status: 'healthy' | 'unhealthy';
  timestamp: string;
  services: {
    redis: string;
    kafka: string;
    mongodb: string;
    arangodb: string;
    KVStoreservice: string;
  };
}

export function createHealthRouter(
  container: Container,
  knowledgeBaseContainer: Container,
  configurationManagerContainer: Container
): Router {
  const router = Router();
  const redis = container.get<RedisService>(TYPES.RedisService);
  const kafka = container.get<TokenEventProducer>(TYPES.TokenEventProducer);
  const mongooseService = container.get<MongoService>(TYPES.MongoService);
  const arangoService = knowledgeBaseContainer.get<ArangoService>(
    TYPES.ArangoService,
  );
  const keyValueStoreService = configurationManagerContainer.get<KeyValueStoreService>(
    TYPES.KeyValueStoreService,
  );

  const appConfig = container.get<AppConfig>('AppConfig');

  router.get('/', async (_req, res, next) => {
    try {
      const health: HealthStatus = {
        status: 'healthy',
        timestamp: new Date().toISOString(),
        services: {
          redis: 'unknown',
          kafka: 'unknown',
          mongodb: 'unknown',
          arangodb: 'unknown',
          KVStoreservice: 'unknown',
        },
      };

      try {
        await redis.get('health-check');
        health.services.redis = 'healthy';
      } catch (error) {
        health.services.redis = 'unhealthy';
        health.status = 'unhealthy';
      }

      try {
        await kafka.healthCheck();
        health.services.kafka = 'healthy';
      } catch (error) {
        health.services.kafka = 'unhealthy';
        health.status = 'unhealthy';
      }

      try {
        const isMongoHealthy = await mongooseService.healthCheck();
        health.services.mongodb = isMongoHealthy ? 'healthy' : 'unhealthy';
        if (!isMongoHealthy) {
          health.status = 'unhealthy';
        }
      } catch (error) {
        health.services.mongodb = 'unhealthy';
        health.status = 'unhealthy';
      }

      try {
        const isArangoHealthy = await arangoService.isConnected();
        health.services.arangodb = isArangoHealthy ? 'healthy' : 'unhealthy';
        if (!isArangoHealthy) {
          health.status = 'unhealthy';
        }
      } catch (exception) {
        health.services.arangodb = 'unhealthy';
        health.status = 'unhealthy';
      }

      try {
        const isKVServiceHealthy = keyValueStoreService.isConnected();
        health.services.KVStoreservice = isKVServiceHealthy ? 'healthy' : 'unhealthy';
        if (!isKVServiceHealthy) {
          health.status = 'unhealthy';
        }
      } catch (exception) {
        health.services.KVStoreservice = 'unhealthy';
        health.status = 'unhealthy';
      }


      res.status(200).json(health);
    } catch (exception: any) {
      logger.error("health check status failed", exception.message);
      next()
    }
  });

  // Combined services health check (Python query + connector services)
  router.get('/services', async (_req, res, _next) => {
    try {
      const aiHealthUrl = `${appConfig.aiBackend}/health`;
      const connectorHealthUrl = `${appConfig.connectorBackend}/health`;

      const [aiResp, connectorResp] = await Promise.allSettled([
        axios.get(aiHealthUrl, { timeout: 3000 }),
        axios.get(connectorHealthUrl, { timeout: 3000 }),
      ]);

      const isServiceHealthy = (res: PromiseSettledResult<any>) => 
        res.status === 'fulfilled' && res.value.status === 200 && res.value.data?.status === 'healthy';
      
      const aiOk = isServiceHealthy(aiResp);
      const connectorOk = isServiceHealthy(connectorResp);

      const overallHealthy = aiOk && connectorOk;

      res.status(overallHealthy ? 200 : 503).json({
        status: overallHealthy ? 'healthy' : 'unhealthy',
        timestamp: new Date().toISOString(),
        services: {
          query: aiOk ? 'healthy' : 'unhealthy',
          connector: connectorOk ? 'healthy' : 'unhealthy',
        },
      });
    } catch (error: any) {
      logger.error('Combined services health check failed', error?.message ?? error);
      res.status(503).json({
        status: 'unhealthy',
        timestamp: new Date().toISOString(),
        services: {
          query: 'unknown',
          connector: 'unknown',
        },
      });
    }
  });

  return router;
}
