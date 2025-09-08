import { ConfigService } from '../services/cm.service';

export interface AppConfig {
  jwtSecret: string;
  scopedJwtSecret: string;
  cookieSecret: string;
  rsAvailable: string;

  communicationBackend: string;
  frontendUrl: string;
  iamBackend: string;
  authBackend: string;
  cmBackend: string;
  kbBackend: string;
  esBackend: string;
  storageBackend: string;
  tokenBackend: string;
  aiBackend: string;
  connectorBackend: string;
  connectorPublicUrl: string;
  indexingBackend: string;
  kafka: {
    brokers: string[];
    sasl?: {
      mechanism: 'plain' | 'scram-sha-256' | 'scram-sha-512';
      username: string;
      password: string;
    };
  };
  redis: {
    host: string;
    port: number;
    password?: string;
    db?: number;
  };
  mongo: {
    uri: string;
    db: string;
  };

  qdrant: {
    port: number;
    apiKey: string;
    host: string;
    grpcPort: number;
  };

  arango: {
    url: string;
    db: string;
    username: string;
    password: string;
  };
  etcd: {
    host: string;
    port: number;
    dialTimeout: number;
  };

  smtp: {
    host: string;
    port: number;
    username?: string;
    password?: string;
    fromEmail: string;
  } | null;
  storage: {
    storageType: string;
    endpoint: string;
  };
}

export const loadAppConfig = async (): Promise<AppConfig> => {
  const configService = ConfigService.getInstance();

  return {
    jwtSecret: await configService.getJwtSecret(),
    scopedJwtSecret: await configService.getScopedJwtSecret(),
    cookieSecret: await configService.getCookieSecret(),
    rsAvailable: await configService.getRsAvailable(),

    frontendUrl: await configService.getFrontendUrl(),
    communicationBackend: await configService.getCommunicationBackendUrl(),
    kbBackend: await configService.getKbBackendUrl(),
    esBackend: await configService.getEsBackendUrl(),
    iamBackend: await configService.getIamBackendUrl(),
    authBackend: await configService.getAuthBackendUrl(),
    cmBackend: await configService.getCmBackendUrl(),
    connectorBackend: await configService.getConnectorUrl(),
    connectorPublicUrl: await configService.getConnectorPublicUrl(),
    indexingBackend: await configService.getIndexingUrl(),
    aiBackend: await configService.getAiBackendUrl(),
    storageBackend: await configService.getStorageBackendUrl(),
    tokenBackend: await configService.getTokenBackendUrl(),
    kafka: await configService.getKafkaConfig(),
    redis: await configService.getRedisConfig(),
    arango: await configService.getArangoConfig(),
    qdrant: await configService.getQdrantConfig(),
    mongo: await configService.getMongoConfig(),
    smtp: await configService.getSmtpConfig(),

    etcd: {
      host: process.env.ETCD_HOST!,
      port: parseInt(process.env.ETCD_PORT!, 10),
      dialTimeout: parseInt(process.env.ETCD_DIAL_TIMEOUT!, 10),
    },
    storage: await configService.getStorageConfig(),
  };
};
