import { inject, injectable } from 'inversify';
import {
  S3StorageConfig,
  AzureBlobStorageConfig,
  LocalStorageConfig,
} from './config/storage.config';
import AmazonS3Adapter from './providers/s3.provider';
import LocalStorageAdapter from './providers/local-storage.provider';
import { KeyValueStoreService } from '../../libs/services/keyValueStore.service';
import { storageEtcdPaths } from './constants/constants';
import { storageTypes } from '../configuration_manager/constants/constants';
import { Logger } from '../../libs/services/logger.service';
import AzureBlobStorageAdapter from './providers/azure.provider';
import { StorageServiceAdapter } from './adapter/base-storage.adapter';
import { DefaultStorageConfig } from '../tokens_manager/services/cm.service';
const logger = Logger.getInstance({ service: 'StorageService' });
@injectable()
export class StorageService {
  private adapter!: StorageServiceAdapter;
  private isInitialized: boolean = false;

  constructor(
    @inject('KeyValueStoreService')
    private readonly keyValueStoreService: KeyValueStoreService,
    private readonly config:
      | S3StorageConfig
      | AzureBlobStorageConfig
      | LocalStorageConfig,
    private readonly defaultConfig: DefaultStorageConfig,
  ) {}

  public async initialize(): Promise<void> {
    if (!this.isInitialized) {
      try {
        const storageConfig =
          (await this.keyValueStoreService.get<string>(storageEtcdPaths)) ||
          '{}';
        const { storageType } = JSON.parse(storageConfig) || storageTypes.LOCAL;
        this.adapter = await this.initializeStorageAdapter(storageType);
        this.isInitialized = true;
      } catch (error) {
        logger.error('Failed to initialize storage service', {
          error: error instanceof Error ? error.message : 'Unknown error',
        });
        throw error;
      }
    }
  }

  private async initializeStorageAdapter(storageType: string) {
    switch (storageType) {
      case 's3':
        return new StorageServiceAdapter(
          new AmazonS3Adapter(this.extractS3Credentials()),
        );
      case 'azureBlob':
        return new StorageServiceAdapter(
          new AzureBlobStorageAdapter(this.extractAzureBlobCredentials()),
        );
      case 'local':
        return new StorageServiceAdapter(
          new LocalStorageAdapter(this.extractLocalStorageCredentials()),
        );
      default:
        throw new Error(`Unsupported storage type: ${storageType}`);
    }
  }

  private extractLocalStorageCredentials() {
    const configuration = this.config as LocalStorageConfig;
    return {
      mountName: configuration.mountName || 'PipesHub',
      baseUrl: configuration.baseUrl || this.defaultConfig.endpoint,
    };
  }

  private extractS3Credentials() {
    const configuration = this.config as S3StorageConfig;
    return {
      accessKeyId: configuration.accessKeyId,
      secretAccessKey: configuration.secretAccessKey,
      region: configuration.region,
      bucket: configuration.bucketName,
    };
  }

  private extractAzureBlobCredentials() {
    const configuration = this.config as Partial<AzureBlobStorageConfig>;
    return {
      azureBlobConnectionString: configuration.azureBlobConnectionString,
      accountName: configuration.accountName,
      accountKey: configuration.accountKey,
      containerName: configuration.containerName,
      endpointProtocol: configuration.endpointProtocol,
      endpointSuffix: configuration.endpointSuffix,
    };
  }

  public getAdapter(): StorageServiceAdapter {
    return this.adapter;
  }

  public isConnected(): boolean {
    return this.isInitialized;
  }

  public async disconnect(): Promise<void> {
    try {
      this.isInitialized = false;
      logger.info('Storage service disconnected successfully');
    } catch (error) {
      logger.error('Error disconnecting storage service', {
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      throw error;
    }
  }
}
