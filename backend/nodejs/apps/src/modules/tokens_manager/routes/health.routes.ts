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

/**
 * Convert a Neo4j Bolt/routing URI into the HTTP(S) discovery endpoint used
 * for the health probe. Handles every scheme the Neo4j driver supports
 * (`bolt`, `bolt+s`, `bolt+ssc`, `neo4j`, `neo4j+s`, `neo4j+ssc`), the
 * presence/absence of an explicit port, embedded credentials, and TLS.
 *
 * Mapping rules:
 *   - `bolt`, `neo4j`                         → `http://host:<httpPort>/`
 *   - `bolt+s`, `neo4j+s`, `+ssc` variants    → `https://host:<httpPort>/`
 *   - `http(s)://…` passed through unchanged (already an HTTP URL)
 *   - Port: explicit port → Bolt default 7687 mapped to 7474; any other
 *           Bolt port is passed through (user must expose matching HTTP port);
 *           no port → stock HTTP port (7474 / 7473)
 */
function buildNeo4jHttpUrl(uri: string): string {
  // Split scheme manually — `new URL('bolt+s://…')` throws in some Node builds.
  const match = uri.match(/^([a-z0-9+]+):\/\/(.*)$/i);
  if (!match || !match[1] || match[2] === undefined) {
    return uri; // give up; let axios fail and the catch block mark unhealthy
  }
  const scheme = match[1].toLowerCase();
  const rest = match[2];

  if (scheme === 'http' || scheme === 'https') {
    return uri;
  }

  const secure = scheme.endsWith('+s') || scheme.endsWith('+ssc');
  const httpScheme = secure ? 'https' : 'http';

  // Strip any embedded credentials so we don't leak them into the health URL;
  // the axios `auth` option below handles auth explicitly.
  const authStripped = rest.replace(/^[^@/]*@/, '');

  // Separate host[:port] from the (unused) path. `split` always returns at
  // least one element, but `noUncheckedIndexedAccess` still types the index
  // access as possibly undefined; fall back to an empty string to satisfy TS.
  const hostPort = authStripped.split('/', 1)[0] ?? '';
  const lastColon = hostPort.lastIndexOf(':');
  const closingBracket = hostPort.lastIndexOf(']'); // IPv6 literal
  let host: string;
  let port: string | null;
  if (lastColon > closingBracket) {
    host = hostPort.slice(0, lastColon);
    port = hostPort.slice(lastColon + 1);
  } else {
    host = hostPort;
    port = null;
  }

  // Bolt default 7687 -> HTTP 7474 / HTTPS 7473. Other Bolt ports are passed
  // through unchanged (users with custom ports must expose matching HTTP).
  let httpPort: string;
  if (port === '7687' || port === null) {
    httpPort = secure ? '7473' : '7474';
  } else {
    httpPort = port;
  }

  return `${httpScheme}://${host}:${httpPort}/`;
}

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
          const neo4jHttpUrl = buildNeo4jHttpUrl(
            process.env.NEO4J_URI || 'bolt://localhost:7687',
          );
          const neo4jUser = process.env.NEO4J_USERNAME || 'neo4j';
          const neo4jPass = process.env.NEO4J_PASSWORD;
          const neo4jResp = await axios.get(neo4jHttpUrl, {
            timeout: 3000,
            // `GET /` is public in stock Neo4j, but pass creds anyway so deployments
            // that restrict the HTTP discovery endpoint still return 200.
            auth: neo4jPass ? { username: neo4jUser, password: neo4jPass } : undefined,
            validateStatus: () => true,
          });
          services.graphDb = neo4jResp.status === 200 ? 'healthy' : 'unhealthy';
          if (neo4jResp.status !== 200) overallHealthy = false;
        } catch (error) {
          services.graphDb = 'unhealthy';
          overallHealthy = false;
        }
      } else {
        try {
          // Arango's /_api/version requires Basic Auth when server authentication
          // is enabled (the default). Without credentials it returns 401 and the
          // probe falsely reports unhealthy even though the DB is fine.
          const arangoResp = await axios.get(`${appConfig.arango.url}/_api/version`, {
            timeout: 3000,
            auth:
              appConfig.arango.username || appConfig.arango.password
                ? {
                    username: appConfig.arango.username,
                    password: appConfig.arango.password,
                  }
                : undefined,
            validateStatus: () => true,
          });
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
