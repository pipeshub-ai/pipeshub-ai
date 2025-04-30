import { Logger } from './logger.service';
import { IKVStoreConnection } from '../types/keyValueStore.types';
import { DistributedKeyValueStore } from '../keyValueStore/keyValueStore';
import { KeyValueStoreFactory } from '../keyValueStore/keyValueStoreFactory';
import {
  KeyValueStoreType,
  StoreType,
} from '../keyValueStore/constants/KeyValueStoreType';
import { ConfigurationManagerConfig } from '../../modules/configuration_manager/config/config';

export class KeyValueStoreService implements IKVStoreConnection {
  private static instance: KeyValueStoreService;
  private store!: DistributedKeyValueStore<any>;
  private isInitialized = false;
  private logger = Logger.getInstance({
    service: 'KeyValueStoreService',
  });
  private config: ConfigurationManagerConfig;

  private constructor(config: ConfigurationManagerConfig) {
    this.config = config;
  }

  // Returns the single instance; initializes it if not already created.
  public static getInstance(
    config: ConfigurationManagerConfig,
  ): KeyValueStoreService {
    if (!KeyValueStoreService.instance) {
      KeyValueStoreService.instance = new KeyValueStoreService(config);
    }
    return KeyValueStoreService.instance;
  }

  /**
   * Establishes connection to the key-value store and initializes the store instance
   * Creates a new store instance using the factory with default serialization/deserialization
   */
  async connect(): Promise<void> {
    try {
      this.logger.info('Connecting to key-value store');
      const storeType: StoreType = KeyValueStoreType.fromString(
        this.config.storeType,
      );

      if (!this.isInitialized) {
        this.store = KeyValueStoreFactory.createStore(
          storeType,
          this.config.storeConfig,
          // (value: any) => Buffer.from(JSON.stringify(value)),
          // (buffer: Buffer) => JSON.parse(buffer.toString()),
          (value: any) => Buffer.from(value),
          (buffer: Buffer) => buffer.toString(),
        );
        this.isInitialized = true;
        this.logger.info('Successfully connected to key-value store');
      }
    } catch (error: any) {
      this.isInitialized = false;
      this.logger.error('Failed to connect to key-value store', { error });
      throw error;
    }
  }

  /**
   * Closes the connection to the key-value store
   * Resets initialization state and logs disconnection
   */
  async disconnect(): Promise<void> {
    this.isInitialized = false;
    this.logger.info('Disconnected from key-value store');
  }

  /**
   * Checks if the store is currently connected and initialized
   * @returns boolean indicating connection status
   */
  isConnected(): boolean {
    return this.isInitialized;
  }

  /**
   * Ensures store connection is established before operations
   * Attempts to connect if not already initialized
   */
  private async ensureConnection(): Promise<void> {
    if (!this.isConnected()) {
      await this.connect();
    }
  }

  /**
   * Sets a value in the store, creating new key or updating existing
   * @param key - The key to set
   * @param value - The value to store
   */
  async set<T>(key: string, value: T): Promise<void> {
    await this.ensureConnection();
    try {
      await this.store.createKey(key, value);
    } catch (error) {
      if (error instanceof Error && error.message.includes('already exists')) {
        await this.store.updateValue(key, value);
      } else {
        throw error;
      }
    }
  }

  /**
   * Retrieves a value from the store by key
   * @param key - The key to retrieve
   * @returns The stored value or null if not found
   */
  async get<T>(key: string): Promise<T | null> {
    await this.ensureConnection();
    return await this.store.getKey(key);
  }

  /**
   * Removes a key-value pair from the store
   * @param key - The key to delete
   */
  async delete(key: string): Promise<void> {
    await this.ensureConnection();
    await this.store.deleteKey(key);
  }

  /**
   * Lists all keys under a specific directory prefix
   * @param directory - The directory prefix to search
   * @returns Array of matching keys
   */
  async listKeysInDirectory(directory: string): Promise<string[]> {
    await this.ensureConnection();
    return await this.store.listKeysInDirectory(directory);
  }

  /**
   * Sets up a watch on a specific key for value changes
   * @param key - The key to watch
   * @param callback - Function to call when value changes
   */
  async watchKey<T>(
    key: string,
    callback: (value: T | null) => void,
  ): Promise<void> {
    await this.ensureConnection();
    await this.store.watchKey(key, callback);
  }

  /**
   * Retrieves all keys currently in the store
   * @returns Array of all keys
   */
  async getAllKeys(): Promise<string[]> {
    await this.ensureConnection();
    return await this.store.getAllKeys();
  }
}
