import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { ARANGO_DB_NAME, MONGO_DB_NAME } from '../../../libs/enums/db.enum';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { loadConfigurationManagerConfig } from '../../configuration_manager/config/config';
import { configPaths } from '../../configuration_manager/paths/paths';
import { normalizeUrl } from '../utils/utils';
import * as crypto from 'crypto';
import { promisify } from 'util';

const generateKeyPair = promisify(crypto.generateKeyPair);

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

  /**
   * Initialize RSA keys in etcd if they don't exist
   */
  public async initializeRSAKeys(): Promise<void> {
    try {
      // Get the JWT algorithm setting
      const jwtAlgorithm = process.env.JWT_ENCRYPTION_KEY || 'HS256';
      
      if (jwtAlgorithm !== 'RS256') {
        console.log('JWT algorithm is not RS256, skipping RSA key generation');
        return;
      }

      console.log('Checking for RSA keys in etcd...');

      // Get existing secret keys from etcd
      const encryptedSecretKeys = await this.keyValueStoreService.get<string>(
        configPaths.secretKeys
      );

      let secretKeys: Record<string, string> = {};
      
      if (encryptedSecretKeys) {
        try {
          secretKeys = JSON.parse(this.encryptionService.decrypt(encryptedSecretKeys));
        } catch (error) {
          console.error('Failed to decrypt existing secret keys:', error);
          secretKeys = {};
        }
      }

      let keysGenerated = false;

      // Check and generate JWT RSA keys if missing
      if (!secretKeys.jwtPrivateKey || !secretKeys.jwtPublicKey) {
        console.log('Generating JWT RSA key pair...');
        const jwtKeyPair = await this.generateRSAKeyPair();
        secretKeys.jwtPrivateKey = jwtKeyPair.privateKey;
        secretKeys.jwtPublicKey = jwtKeyPair.publicKey;
        keysGenerated = true;
      }

      // Check and generate Scoped JWT RSA keys if missing
      if (!secretKeys.scopedJwtPrivateKey || !secretKeys.scopedJwtPublicKey) {
        console.log('Generating Scoped JWT RSA key pair...');
        const scopedJwtKeyPair = await this.generateRSAKeyPair();
        secretKeys.scopedJwtPrivateKey = scopedJwtKeyPair.privateKey;
        secretKeys.scopedJwtPublicKey = scopedJwtKeyPair.publicKey;
        keysGenerated = true;
      }

      // Save back to etcd if any keys were generated
      if (keysGenerated) {
        const encryptedKeys = this.encryptionService.encrypt(
          JSON.stringify(secretKeys)
        );
        
        await this.keyValueStoreService.set(
          configPaths.secretKeys,
          encryptedKeys
        );
        
        console.log('✓ RSA keys successfully generated and stored in etcd');
      } else {
        console.log('✓ RSA keys already exist in etcd');
      }
    } catch (error) {
      console.error('Failed to initialize RSA keys:', error);
      throw error;
    }
  }

  /**
   * Generate an RSA key pair
   */
  private async generateRSAKeyPair(): Promise<{ publicKey: string; privateKey: string }> {
    const { publicKey, privateKey } = await generateKeyPair('rsa', {
      modulusLength: 2048,
      publicKeyEncoding: {
        type: 'spki',
        format: 'pem'
      },
      privateKeyEncoding: {
        type: 'pkcs8',
        format: 'pem'
      }
    });

    return { publicKey, privateKey };
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
        normalizeUrl(parsedUrl.auth?.endpoint) ||
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
        normalizeUrl(parsedUrl.communication?.endpoint) ||
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
        normalizeUrl(parsedUrl.kb?.endpoint) ||
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
        normalizeUrl(parsedUrl.es?.endpoint) ||
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
        normalizeUrl(parsedUrl.cm?.endpoint) ||
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
        normalizeUrl(parsedUrl.tokenBackend?.endpoint) ||
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
      endpoint: normalizeUrl(process.env.CONNECTOR_BACKEND!) || normalizeUrl(parsedUrl.connectors?.endpoint),
      publicEndpoint:
        normalizeUrl(process.env.CONNECTOR_PUBLIC_BACKEND!) ||
        normalizeUrl(parsedUrl.connectors?.publicEndpoint),
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
    return normalizeUrl(parsedUrl.connectors.publicEndpoint) || normalizeUrl(process.env.CONNECTOR_PUBLIC_BACKEND!);
  }

  public async getIndexingUrl(): Promise<string> {
    const url =
      (await this.keyValueStoreService.get<string>(configPaths.endpoint)) ||
      '{}';

    let parsedUrl = JSON.parse(url);

    // Preserve existing `auth` object if it exists, otherwise create a new one
    parsedUrl.indexing = {
      ...parsedUrl.indexing,
      endpoint: normalizeUrl(process.env.INDEXING_BACKEND!) || normalizeUrl(parsedUrl.indexing?.endpoint),
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
        normalizeUrl(parsedUrl.iam?.endpoint) ||
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
        normalizeUrl(parsedUrl.storage?.endpoint) ||
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
        normalizeUrl(process.env.FRONTEND_PUBLIC_URL!) ||
        normalizeUrl(parsedUrl.frontend?.publicEndpoint) ||
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
        normalizeUrl(process.env.QUERY_BACKEND!) ||
        normalizeUrl(parsedUrl.queryBackend?.endpoint) ||
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
        normalizeUrl(parsedUrl.storage?.endpoint) ||
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

  // Get JWT Algorithm
  public async getJwtAlgorithm(): Promise<'HS256' | 'RS256'> {
    const encryptionKey = process.env.JWT_ENCRYPTION_KEY;
    return encryptionKey === 'RS256' ? 'RS256' : 'HS256';
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

  // Get JWT Private Key (RS256)
  public async getJwtPrivateKey(): Promise<string> {
    // First check if key is provided as base64 encoded string
    if (process.env.JWT_PRIVATE_KEY) {
      return Buffer.from(process.env.JWT_PRIVATE_KEY, 'base64').toString('utf-8');
    }
    
    // Check etcd for the key
    const encryptedSecretKeys = await this.keyValueStoreService.get<string>(
      configPaths.secretKeys,
    );
    
    if (encryptedSecretKeys) {
      try {
        const parsedKeys = JSON.parse(
          this.encryptionService.decrypt(encryptedSecretKeys),
        );
        if (parsedKeys.jwtPrivateKey) {
          return parsedKeys.jwtPrivateKey;
        }
      } catch (error) {
        throw new Error(`Failed to get JWT private key from etcd: ${error}`);
      }
    }
    
    // Otherwise, read from file path
    const keyPath = process.env.JWT_PRIVATE_KEY_PATH || './keys/jwt-private.pem';
    const fs = await import('fs');
    const path = await import('path');
    
    try {
      const absolutePath = path.resolve(keyPath);
      return fs.readFileSync(absolutePath, 'utf-8');
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      throw new Error(`Failed to load JWT private key from ${keyPath}: ${errorMessage}`);
    }
  }

  // Get JWT Public Key (RS256)
  public async getJwtPublicKey(): Promise<string> {
    // First check if key is provided as base64 encoded string
    if (process.env.JWT_PUBLIC_KEY) {
      return Buffer.from(process.env.JWT_PUBLIC_KEY, 'base64').toString('utf-8');
    }
    
    // Check etcd for the key
    const encryptedSecretKeys = await this.keyValueStoreService.get<string>(
      configPaths.secretKeys,
    );
    
    if (encryptedSecretKeys) {
      try {
        const parsedKeys = JSON.parse(
          this.encryptionService.decrypt(encryptedSecretKeys),
        );
        if (parsedKeys.jwtPublicKey) {
          return parsedKeys.jwtPublicKey;
        }
      } catch (error) {
        throw new Error(`Failed to get JWT public key from etcd: ${error}`);
      }
    }
    
    // Otherwise, read from file path
    const keyPath = process.env.JWT_PUBLIC_KEY_PATH || './keys/jwt-public.pem';
    const fs = await import('fs');
    const path = await import('path');
    
    try {
      const absolutePath = path.resolve(keyPath);
      return fs.readFileSync(absolutePath, 'utf-8');
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      throw new Error(`Failed to load JWT public key from ${keyPath}: ${errorMessage}`);
    }
  }

  // Get Scoped JWT Private Key (RS256)
  public async getScopedJwtPrivateKey(): Promise<string> {
    // First check if key is provided as base64 encoded string
    if (process.env.JWT_SCOPED_PRIVATE_KEY || process.env.SCOPED_JWT_PRIVATE_KEY) {
      const key = process.env.JWT_SCOPED_PRIVATE_KEY || process.env.SCOPED_JWT_PRIVATE_KEY;
      return Buffer.from(key!, 'base64').toString('utf-8');
    }
    
    // Check etcd for the key
    const encryptedSecretKeys = await this.keyValueStoreService.get<string>(
      configPaths.secretKeys,
    );
    
    if (encryptedSecretKeys) {
      try {
        const parsedKeys = JSON.parse(
          this.encryptionService.decrypt(encryptedSecretKeys),
        );
        if (parsedKeys.scopedJwtPrivateKey) {
          return parsedKeys.scopedJwtPrivateKey;
        }
      } catch (error) {
        throw new Error(`Failed to get scoped JWT private key from etcd: ${error}`);
      }
    }
    
    // Otherwise, read from file path
    const keyPath = process.env.JWT_SCOPED_PRIVATE_KEY_PATH || process.env.SCOPED_JWT_PRIVATE_KEY_PATH || './keys/jwt-scoped-private.pem';
    const fs = await import('fs');
    const path = await import('path');
    
    try {
      const absolutePath = path.resolve(keyPath);
      return fs.readFileSync(absolutePath, 'utf-8');
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      throw new Error(`Failed to load scoped JWT private key from ${keyPath}: ${errorMessage}`);
    }
  }

  // Get Scoped JWT Public Key (RS256)
  public async getScopedJwtPublicKey(): Promise<string> {
    // First check if key is provided as base64 encoded string
    if (process.env.JWT_SCOPED_PUBLIC_KEY || process.env.SCOPED_JWT_PUBLIC_KEY) {
      const key = process.env.JWT_SCOPED_PUBLIC_KEY || process.env.SCOPED_JWT_PUBLIC_KEY;
      return Buffer.from(key!, 'base64').toString('utf-8');
    }
    
    // Check etcd for the key
    const encryptedSecretKeys = await this.keyValueStoreService.get<string>(
      configPaths.secretKeys,
    );
    
    if (encryptedSecretKeys) {
      try {
        const parsedKeys = JSON.parse(
          this.encryptionService.decrypt(encryptedSecretKeys),
        );
        if (parsedKeys.scopedJwtPublicKey) {
          return parsedKeys.scopedJwtPublicKey;
        }
      } catch (error) {
        throw new Error(`Failed to get scoped JWT public key from etcd: ${error}`);
      }
    }
    
    // Otherwise, read from file path
    const keyPath = process.env.JWT_SCOPED_PUBLIC_KEY_PATH || process.env.SCOPED_JWT_PUBLIC_KEY_PATH || './keys/jwt-scoped-public.pem';
    const fs = await import('fs');
    const path = await import('path');
    
    try {
      const absolutePath = path.resolve(keyPath);
      return fs.readFileSync(absolutePath, 'utf-8');
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : 'Unknown error';
      throw new Error(`Failed to load scoped JWT public key from ${keyPath}: ${errorMessage}`);
    }
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
