import { Etcd3 } from 'etcd3';
import { DistributedKeyValueStore } from '../keyValueStore';
import { ConfigurationManagerStoreConfig } from '../../../modules/configuration_manager/config/config';
import { KeyAlreadyExistsError, KeyNotFoundError } from '../../errors/etcd.errors';
import { Logger } from '../../services/logger.service';
export class Etcd3DistributedKeyValueStore<T> implements DistributedKeyValueStore<T>
{
  private client: Etcd3;
  private serializer;
  private deserializer;

  constructor(
    config: ConfigurationManagerStoreConfig,
    serializer: (value: T) => Buffer,
    deserializer: (buffer: Buffer) => T,
  ) {
    const hostWithPort = config.port ? `${config.host}:${config.port}` : config.host;

    this.client = new Etcd3({
      hosts: [hostWithPort],
      dialTimeout: config.dialTimeout,
    });
    this.serializer = serializer;
    this.deserializer = deserializer;
  }

  async createKey(key: string, value: T): Promise<void> {
    const existingValue = await this.client.get(key).buffer();
    if (existingValue !== null) {
      throw new KeyAlreadyExistsError(`Key "${key}" already exists.`);
    }
    await this.client.put(key).value(this.serializer(value));
  }

  async updateValue(key: string, value: T): Promise<void> {
    const existingValue = await this.client.get(key).buffer();
    if (existingValue === null) {
      throw new KeyNotFoundError(`Key "${key}" does not exist.`);
    }
    await this.client.put(key).value(this.serializer(value));
  }

  async getKey(key: string): Promise<T | null> {
    const buffer = await this.client.get(key).buffer();
    return buffer ? this.deserializer(buffer) : null;
  }

  async deleteKey(key: string): Promise<void> {
    await this.client.delete().key(key);
  }

  async getAllKeys(): Promise<string[]> {
    return await this.client.getAll().keys();
  }

  async watchKey(
    key: string,
    callback: (value: T | null) => void,
  ): Promise<void> {
    const watcher = await this.client.watch().key(key).create();
    watcher.on('put', (res) =>
      callback(this.deserializer(Buffer.from(res.value))),
    );
    watcher.on('delete', () => callback(null));
  }

  async listKeysInDirectory(directory: string): Promise<string[]> {
    return await this.client.getAll().prefix(directory).keys();
  }

  async compareAndSet(key: string, expectedValue: T | null, newValue: T): Promise<boolean> {
    const newBuffer = this.serializer(newValue);
    const expectedBuffer = expectedValue !== null ? this.serializer(expectedValue) : null;

    try {
      // Get current value
      const currentValue = await this.client.get(key).buffer();

      // Compare buffers directly for exact match
      const valuesMatch =
        (expectedValue === null && currentValue === null) ||
        (expectedBuffer !== null && currentValue !== null && expectedBuffer.equals(currentValue));

      if (!valuesMatch) {
        // Values don't match, CAS failed
        return false;
      }

      await this.client.put(key).value(newBuffer);
      
      // Verify the update succeeded by reading back
      const updatedValue = await this.client.get(key).buffer();
      return updatedValue !== null && updatedValue.equals(newBuffer);
    } catch (error) {
      // If update fails, return false
      return false;
    }
  }

  /**
   * Health check for etcd store.
   * TODO: Remove this method when all deployments migrate to Redis KV store.
   */
  async healthCheck(): Promise<boolean> {
    try {
      await this.client.maintenance.status();
      return true;
    } catch (error) {
      return false;
    }
  }

  async publishCacheInvalidation(key: string): Promise<void> {
    Logger.getInstance().warn(
      `Cache invalidation for key ${key} is not implemented for etcd`,
      { service: 'Etcd3DistributedKeyValueStore' },
    );
  }
}
