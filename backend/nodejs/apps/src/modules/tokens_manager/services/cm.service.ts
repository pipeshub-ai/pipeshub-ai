import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { ARANGO_DB_NAME, MONGO_DB_NAME } from '../../../libs/enums/db.enum';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { loadConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { configPaths } from '../../configuration_manager/paths/paths';

// Define interfaces for all service configurations
export interface SmtpConfig {
  host: string;
  port: number;
  username?: string;
  password?: string;
  fromEmail: string;
}

export const randomKeyGenerator = () => {
  const chars =
    'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
  let result = '';
  for (let i = 0; i < 20; i++) {
    result += chars.charAt(Math.floor(Math.random() * chars.length));
  }
  return result;
};

export interface KafkaConfig {
  brokers: string[];
  sasl?: {
    mechanism: 'plain' | 'scram-sha-256' | 'scram-sha-512';
    username: string;
    password: string;
  };
}

export interface RedisConfig {
  host: string;
  port: number;
  password?: string;
  db?: number;
}

export interface MongoConfig {
  uri: string;
  db: string;
}

export interface QdrantConfig {
  port: number;
  apiKey: string;
  host: string;
  grpcPort: number;
}

export interface ArangoConfig {
  url: string;
  db: string;
  username: string;
  password: string;
}

export interface EtcdConfig {
  host: string;
  port: number;
  dialTimeout: number;
}

export interface EncryptionConfig {
  key: string;
  algorithm: string;
}

export interface DefaultStorageConfig {
  storageType: string;
  endpoint: string;
}

// Main Config Service
export class ConfigService {
  private static instance: ConfigService;
  private keyValueStoreService: KeyValueStoreService;
  private configManagerConfig: any;
  private encryptionService: EncryptionService;

  private constructor() {
    this.configManagerConfig = loadConfigurationManagerConfig();
    this.keyValueStoreService = KeyValueStoreService.getInstance(
      this.configManagerConfig,
    );
    this.encryptionService = EncryptionService.getInstance(
      this.configManagerConfig.algorithm,
      this.configManagerConfig.secretKey,
    );
  }

  public static getInstance(): ConfigService {
    if (!ConfigService.instance) {
      ConfigService.instance = new ConfigService();
    }
    return ConfigService.instance;
  }

  public async connect(): Promise<void> {
    await this.keyValueStoreService.connect();
  }

  private async getEncryptedConfig<T>(
    configPath: string,
    fallbackEnvVars: Record<string, any>,
  ): Promise<T> {
    try {
      const encryptedConfig =
        await this.keyValueStoreService.get<string>(configPath);

      // If config exists in ETCD
      if (encryptedConfig) {
        return JSON.parse(this.encryptionService.decrypt(encryptedConfig)) as T;
      }
      const fallbackConfig = fallbackEnvVars as T;
      await this.saveConfigToEtcd(configPath, fallbackConfig);

      return fallbackConfig;
    } catch (error) {
      return fallbackEnvVars as T;
    }
  }

  // Save config to ETCD
  private async saveConfigToEtcd<T>(
    configPath: string,
    config: T,
  ): Promise<void> {
    try {
      // Encrypt the config before saving
      const encryptedConfig = this.encryptionService.encrypt(
        JSON.stringify(config),
      );

      // Save to key-value store
      await this.keyValueStoreService.set(configPath, encryptedConfig);
    } catch (error) {
      throw error;
    }
  }

  // SMTP Configuration
  public async getSmtpConfig(): Promise<SmtpConfig | null> {
    const encryptedConfig = await this.keyValueStoreService.get<string>(
      configPaths.smtp,
    );
    if (encryptedConfig) {
      return JSON.parse(this.encryptionService.decrypt(encryptedConfig));
    }
    return null;
  }

  // Kafka Configuration
  public async getKafkaConfig(): Promise<KafkaConfig> {
    return this.getEncryptedConfig<KafkaConfig>(configPaths.broker.kafka, {
      brokers: process.env.KAFKA_BROKERS!.split(','),
      ...(process.env.KAFKA_USERNAME && {
        sasl: {
          mechanism: process.env.KAFKA_SASL_MECHANISM,
          username: process.env.KAFKA_USERNAME,
          password: process.env.KAFKA_PASSWORD!,
        },
      }),
    });
  }

  // Redis Configuration
  public async getRedisConfig(): Promise<RedisConfig> {
    return this.getEncryptedConfig<RedisConfig>(
      configPaths.keyValueStore.redis,
      {
        host: process.env.REDIS_HOST!,
        port: parseInt(process.env.REDIS_PORT!, 10),
        password: process.env.REDIS_PASSWORD,
        db: parseInt(process.env.REDIS_DB || '0', 10),
      },
    );
  }

  // MongoDB Configuration
  public async getMongoConfig(): Promise<MongoConfig> {
    return this.getEncryptedConfig<MongoConfig>(configPaths.db.mongodb, {
      uri: process.env.MONGO_URI!,
      db: MONGO_DB_NAME,
    });
  }

  // Qdrant Configuration
  public async getQdrantConfig(): Promise<QdrantConfig> {
    return this.getEncryptedConfig<QdrantConfig>(configPaths.db.qdrant, {
      apiKey: process.env.QDRANT_API_KEY!,
      host: process.env.QDRANT_HOST || 'localhost',
      port: parseInt(process.env.QDRANT_PORT || '6333', 10),
      grpcPort: parseInt(process.env.QDRANT_GRPC_PORT || '6334', 10),
    });
  }

  // Arango Configuration
  public async getArangoConfig(): Promise<ArangoConfig> {
    return this.getEncryptedConfig<ArangoConfig>(configPaths.db.arangodb, {
      url: process.env.ARANGO_URL!,
      db: ARANGO_DB_NAME,
      username: process.env.ARANGO_USERNAME!,
      password: process.env.ARANGO_PASSWORD!,
    });
  }

  // ETCD Configuration
  public async getEtcdConfig(): Promise<EtcdConfig> {
    return {
      host: process.env.ETCD_HOST!,
      port: parseInt(process.env.ETCD_PORT!, 10),
      dialTimeout: parseInt(process.env.ETCD_DIAL_TIMEOUT!, 10),
    };
  }

  // Get Common Backend URL

  public async getAuthBackendUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.auth = {
      ...parsedUrl.auth,
      endpoint:
        parsedUrl.auth?.endpoint ||
        `http://localhost:${process.env.PORT ?? 3000}`,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );

    return parsedUrl.auth.endpoint;
  }

  public async getCommunicationBackendUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.communication = {
      ...parsedUrl.communication,
      endpoint:
        parsedUrl.communication?.endpoint ||
        `http://localhost:${process.env.PORT ?? 3000}`,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );

    return parsedUrl.communication.endpoint;
  }

  public async getKbBackendUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.kb = {
      ...parsedUrl.kb,
      endpoint:
        parsedUrl.kb?.endpoint ||
        `http://localhost:${process.env.PORT ?? 3000}`,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );

    return parsedUrl.kb.endpoint;
  }

  public async getEsBackendUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.es = {
      ...parsedUrl.es,
      endpoint:
        parsedUrl.es?.endpoint ||
        `http://localhost:${process.env.PORT ?? 3000}`,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );

    return parsedUrl.es.endpoint;
  }

  public async getCmBackendUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.cm = {
      ...parsedUrl.cm,
      endpoint:
        parsedUrl.cm?.endpoint ||
        `http://localhost:${process.env.PORT ?? 3000}`,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );

    return parsedUrl.cm.endpoint;
  }

  public async getTokenBackendUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.tokenBackend = {
      ...parsedUrl.tokenBackend,
      endpoint:
        parsedUrl.tokenBackend?.endpoint ||
        `http://localhost:${process.env.PORT ?? 3000}`,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );

    return parsedUrl.tokenBackend.endpoint;
  }

  public async getConnectorUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.connectors = {
      ...parsedUrl.connectors,
      endpoint: parsedUrl.connectors?.endpoint || process.env.CONNECTOR_BACKEND,
      publicEndpoint:
        parsedUrl.connectors?.publicEndpoint ||
        process.env.CONNECTOR_PUBLIC_BACKEND,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );
    return parsedUrl.connectors.endpoint;
  }
  public async getConnectorPublicUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);
    return parsedUrl.connectors.publicEndpoint || process.env.CONNECTOR_PUBLIC_BACKEND;
  }

  public async getIndexingUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.indexing = {
      ...parsedUrl.indexing,
      endpoint: parsedUrl.indexing?.endpoint || process.env.INDEXING_BACKEND,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );
    return parsedUrl.indexing.endpoint;
  }
  public async getIamBackendUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.iam = {
      ...parsedUrl.iam,
      endpoint:
        parsedUrl.iam?.endpoint ||
        `http://localhost:${process.env.PORT ?? 3000}`,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );

    return parsedUrl.iam.endpoint;
  }
  public async getStorageBackendUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.storage = {
      ...parsedUrl.storage,
      endpoint:
        parsedUrl.storage?.endpoint ||
        `http://localhost:${process.env.PORT ?? 3000}`,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );

    return parsedUrl.storage.endpoint;
  }

  public async getFrontendUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.frontend = {
      ...parsedUrl.frontend,
      publicEndpoint:
        parsedUrl.frontend?.publicEndpoint ||
        process.env.FRONTEND_PUBLIC_URL ||
        `http://localhost:${process.env.PORT ?? 3000}`,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );

    return parsedUrl.frontend.publicEndpoint;
  }

  public async getAiBackendUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.queryBackend = {
      ...parsedUrl.queryBackend,
      endpoint:
        parsedUrl.queryBackend?.endpoint ||
        process.env.QUERY_BACKEND ||
        `http://localhost:8000`,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );

    return parsedUrl.queryBackend.endpoint;
  }

  public async getStorageConfig(): Promise<DefaultStorageConfig> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.storage = {
      ...parsedUrl.storage,
      endpoint:
        parsedUrl.storage?.endpoint ||
        `http://localhost:${process.env.PORT ?? 3000}`,
    };

    // Save the updated object back to configPaths.endpoint
    await this.keyValueStoreService.set<string>(
      configPaths.endpoint,
      JSON.stringify(parsedUrl),
    );
    let storageConfig =
      (await this.keyValueStoreService.get<string>(
        configPaths.storageService,
      )) || '{}';

    const parsedConfig = JSON.parse(storageConfig); // Parse JSON string
    let storageType = parsedConfig.storageType;
    if (!storageType) {
      storageType = 'local';
      await this.keyValueStoreService.set<string>(
        configPaths.storageService,
        JSON.stringify({
          storageType,
        }),
      );
    }
    return { storageType, endpoint: parsedUrl.storage.endpoint };
  }

  // Get JWT Secret
  public async getJwtSecret(): Promise<string> {
    const encryptedSecretKeys = await this.keyValueStoreService.get<string>(
      configPaths.secretKeys,
    );
    let parsedKeys: Record<string, string> = {};
    if (encryptedSecretKeys) {
      parsedKeys = JSON.parse(
        this.encryptionService.decrypt(encryptedSecretKeys),
      );
    }

    if (!parsedKeys || !parsedKeys.jwtSecret) {
      parsedKeys.jwtSecret = randomKeyGenerator();
      const encryptedKeys = this.encryptionService.encrypt(
        JSON.stringify(parsedKeys),
      );
      await this.keyValueStoreService.set(
        configPaths.secretKeys,
        encryptedKeys,
      );
    }
    return parsedKeys.jwtSecret;
  }

  // Get Scoped JWT Secret
  public async getScopedJwtSecret(): Promise<string> {
    const encryptedSecretKeys = await this.keyValueStoreService.get<string>(
      configPaths.secretKeys,
    );
    let parsedKeys: Record<string, string> = {};
    if (encryptedSecretKeys) {
      parsedKeys = JSON.parse(
        this.encryptionService.decrypt(encryptedSecretKeys),
      );
    }
    if (!parsedKeys.scopedJwtSecret) {
      parsedKeys.scopedJwtSecret = randomKeyGenerator();
      const encryptedKeys = this.encryptionService.encrypt(
        JSON.stringify(parsedKeys),
      );
      await this.keyValueStoreService.set(
        configPaths.secretKeys,
        encryptedKeys,
      );
    }

    return parsedKeys.scopedJwtSecret;
  }

  public async getCookieSecret(): Promise<string> {
    const encryptedSecretKeys = await this.keyValueStoreService.get<string>(
      configPaths.secretKeys,
    );
    let parsedKeys: Record<string, string> = {};
    if (encryptedSecretKeys) {
      parsedKeys = JSON.parse(
        this.encryptionService.decrypt(encryptedSecretKeys),
      );
    }
    if (!parsedKeys.cookieSecret) {
      parsedKeys.cookieSecret = randomKeyGenerator();
      const encryptedKeys = this.encryptionService.encrypt(
        JSON.stringify(parsedKeys),
      );
      await this.keyValueStoreService.set(
        configPaths.secretKeys,
        encryptedKeys,
      );
    }

    return parsedKeys.cookieSecret;
  }

  public async getRsAvailable(): Promise<string> {
    if (!process.env.REPLICA_SET_AVAILABLE) {
      const mongoUri = (
        await this.getEncryptedConfig<MongoConfig>(configPaths.db.mongodb, {
          uri: process.env.MONGO_URI!,
          db: MONGO_DB_NAME,
        })
      ).uri;
      if (
        mongoUri.includes('localhost') ||
        mongoUri.includes('@mongodb:27017')
      ) {
        return 'false';
      } else {
        return 'true';
      }
    } else {
      return process.env.REPLICA_SET_AVAILABLE!;
    }
  }
}
