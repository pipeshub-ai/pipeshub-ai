import { Logger } from './logger.service';
import { IKVStoreConnection } from '../types/keyValueStore.types';
import { DistributedKeyValueStore } from '../keyValueStore/keyValueStore';
import type { EtcdAuthAdmin } from '../keyValueStore/etcd-auth';
import { KeyValueStoreFactory } from '../keyValueStore/keyValueStoreFactory';
import {
  KeyValueStoreType,
  StoreType,
} from '../keyValueStore/constants/KeyValueStoreType';
import { ConfigurationManagerConfig } from '../../modules/configuration_manager/config/config';
import { KvValueCodec, kvValueCodec } from '../../edition';

export class KeyValueStoreService implements IKVStoreConnection {
  private static instance: KeyValueStoreService;
  private store!: DistributedKeyValueStore<any>;
  private isInitialized = false;
  private logger = Logger.getInstance({
    service: 'KeyValueStoreService',
  });
  private config: ConfigurationManagerConfig;
  /** The edition's value codec (`kvValueCodec`), read once; null keeps every value as given. */
  private codec: KvValueCodec | null | undefined;

  private valueCodec(): KvValueCodec | null {
    if (this.codec === undefined) this.codec = kvValueCodec();
    return this.codec;
  }

  private encode<T>(key: string, value: T): T {
    const codec = this.valueCodec();
    return codec && typeof value === 'string'
      ? (codec.encode(key, value) as T)
      : value;
  }

  private decode<T>(key: string, stored: T | null): T | null {
    const codec = this.valueCodec();
    return codec && typeof stored === 'string'
      ? (codec.decode(key, stored) as T)
      : stored;
  }

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
   * Retries connection with exponential backoff if the store is unavailable
   */
  async connect(): Promise<void> {
    // Skip if already initialized (singleton pattern)
    if (this.isInitialized) {
      return;
    }

    const maxRetries = 120; // Same as Python services
    const baseDelay = 1000; // 1 second in ms
    const maxDelay = 30000; // 30 seconds max delay
    let lastError: Error | null = null;

    for (let attempt = 1; attempt <= maxRetries; attempt++) {
      try {
        this.logger.info('Connecting to key-value store', {
          storeType: this.config.storeType,
          attempt,
          maxRetries,
        });
        const storeType: StoreType = KeyValueStoreType.fromString(
          this.config.storeType,
        );

        // Select the appropriate config based on store type
        const storeConfig =
          storeType === StoreType.Redis
            ? this.config.redisConfig
            : this.config.storeConfig;

        this.logger.info(`Using ${storeType} as key-value store backend`);

        this.store = KeyValueStoreFactory.createStore(
          storeType,
          storeConfig,
          (value: any) => Buffer.from(value),
          (buffer: Buffer) => buffer.toString(),
        );

        // Verify connection by performing a health check
        const isHealthy = await this.store.healthCheck();
        if (!isHealthy) {
          throw new Error('Key-value store health check failed');
        }

        this.isInitialized = true;
        this.logger.info('Successfully connected to key-value store');
        return;
      } catch (error: any) {
        lastError = error;
        this.isInitialized = false;

        if (attempt < maxRetries) {
          // Calculate delay with exponential backoff
          const delay = Math.min(
            baseDelay * Math.pow(2, attempt - 1),
            maxDelay,
          );
          this.logger.warn(
            `Failed to connect to key-value store (attempt ${attempt}/${maxRetries}). ` +
              `Retrying in ${delay / 1000} seconds...`,
            { error: error.message },
          );
          await this.sleep(delay);
        }
      }
    }

    // All retries failed
    this.logger.error(
      'Failed to connect to key-value store after all retries',
      {
        error: lastError,
      },
    );
    throw lastError;
  }

  /**
   * Helper method to sleep for a given number of milliseconds
   */
  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
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

  /** Whether the configured store is etcd, whose roles Node applies for the sandbox plane (R38IDENT-FU-9). */
  usesEtcd(): boolean {
    return (
      KeyValueStoreType.fromString(this.config.storeType) === StoreType.Etcd3
    );
  }

  /** The store's role-based auth where it has one (etcd with a client certificate); null otherwise or before connect. */
  authAdmin(): EtcdAuthAdmin | null {
    if (!this.isInitialized) return null;
    return this.store.authAdmin?.() ?? null;
  }

  /**
   * Performs a health check on the underlying KV store.
   * For Redis: pings the server
   * For etcd: checks maintenance status
   * @returns boolean indicating health status
   */
  async healthCheck(): Promise<boolean> {
    if (!this.isInitialized) {
      return false;
    }
    try {
      return await this.store.healthCheck();
    } catch (error) {
      this.logger.error('KV store health check failed', { error });
      return false;
    }
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

  private async publishCacheInvalidation(key: string): Promise<void> {
    if (this.store.publishCacheInvalidation) {
      await this.store.publishCacheInvalidation(key);
    }
  }

  /**
   * Sets a value in the store, creating new key or updating existing
   * @param key - The key to set
   * @param value - The value to store
   */
  async set<T>(key: string, given: T): Promise<void> {
    await this.ensureConnection();
    const value = this.encode(key, given);
    try {
      await this.store.createKey(key, value);
    } catch (error) {
      if (error instanceof Error && error.message.includes('already exists')) {
        await this.store.updateValue(key, value);
      } else {
        throw error;
      }
    }
    await this.publishCacheInvalidation(key);
  }

  /**
   * Retrieves a value from the store by key
   * @param key - The key to retrieve
   * @returns The stored value or null if not found
   */
  async get<T>(key: string): Promise<T | null> {
    await this.ensureConnection();
    const kept = (await this.store.getKey(key)) as T | null;
    return this.decode<T>(key, kept);
  }

  /**
   * Removes a key-value pair from the store
   * @param key - The key to delete
   */
  async delete(key: string): Promise<void> {
    await this.ensureConnection();
    await this.store.deleteKey(key);
    await this.publishCacheInvalidation(key);
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
    await this.store.watchKey(
      key,
      this.valueCodec()
        ? (value: T | null) => callback(this.decode<T>(key, value))
        : callback,
    );
  }

  /**
   * Retrieves all keys currently in the store
   * @returns Array of all keys
   */
  async getAllKeys(): Promise<string[]> {
    await this.ensureConnection();
    return await this.store.getAllKeys();
  }

  /**
   * Atomically compares the current value with the expected value and sets the new value if they match.
   * This is a Compare-and-Set (CAS) operation that prevents race conditions.
   * @param key - The key to update
   * @param expectedValue - The expected current value (null if key doesn't exist)
   * @param newValue - The new value to set if the comparison succeeds
   * @returns true if the update was successful (values matched), false otherwise
   */
  async compareAndSet<T>(
    key: string,
    expectedValue: T | null,
    newValue: T,
  ): Promise<boolean> {
    await this.ensureConnection();
    let expected: T | null = expectedValue;
    if (
      this.valueCodec() &&
      (typeof expectedValue === 'string' || expectedValue === null)
    ) {
      // The caller compares against the string it read (decoded); the store compares what it keeps: swap in the kept string
      // when it decodes to exactly the one expected, so the store's own compare stays the atomic step. Anything other than a
      // string is not encoded, so the store compares it as given.
      let kept: T | null;
      try {
        kept = (await this.store.getKey(key)) as T | null;
      } catch (error) {
        // the same contract as the store's own compare-and-set: a failure is `false` (the callers retry), never a rejection
        this.logger.warn('compare-and-set read failed', {
          error: (error as Error).message,
        });
        return false;
      }
      if (this.decode<T>(key, kept) !== expectedValue) return false;
      expected = kept;
    }
    const success = await this.store.compareAndSet(
      key,
      expected,
      this.encode(key, newValue),
    );
    if (success) {
      await this.publishCacheInvalidation(key);
    }
    return success;
  }
}
