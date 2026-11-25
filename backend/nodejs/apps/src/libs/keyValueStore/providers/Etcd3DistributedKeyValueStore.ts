import { Etcd3 } from 'etcd3';
import { DistributedKeyValueStore } from '../keyValueStore';
import { ConfigurationManagerStoreConfig } from '../../../modules/configuration_manager/config/config';
import { KeyAlreadyExistsError, KeyNotFoundError } from '../../errors/etcd.errors';
import { Logger } from '../../services/logger.service';
import { etcdCredentials } from '../etcd-tls';
import { Etcd3AuthAdmin, EtcdAuthAdmin, etcdClientUser } from '../etcd-auth';
export class Etcd3DistributedKeyValueStore<T> implements DistributedKeyValueStore<T>
{
  private client: Etcd3;
  private serializer;
  private deserializer;
  private readonly clientUser: string | null;

  constructor(
    config: ConfigurationManagerStoreConfig,
    serializer: (value: T) => Buffer,
    deserializer: (buffer: Buffer) => T,
  ) {
    const hostWithPort = config.port ? `${config.host}:${config.port}` : config.host;

    const credentials = etcdCredentials(config.tls);
    this.client = new Etcd3({
      hosts: [hostWithPort],
      dialTimeout: config.dialTimeout,
      ...(credentials ? { credentials } : {}),
    });
    this.serializer = serializer;
    this.deserializer = deserializer;
    this.clientUser = etcdClientUser(config.tls);
  }

  /** Role-based auth administration, as the user this client's certificate names; null without a certificate. */
  authAdmin(): EtcdAuthAdmin | null {
    return this.clientUser === null
      ? null
      : new Etcd3AuthAdmin(this.client.auth, this.clientUser);
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
    try {
      // One transaction: a read then a put would overwrite a write made in between (UX2 LC-6).
      // A replace keeps the key's lease (its expiry), as the other stores do (ADR-KEY-01).
      const result =
        expectedValue === null
          ? await this.client
              .if(key, 'Create', '==', 0)
              .then(this.client.put(key).value(newBuffer))
              .commit()
          : await this.client
              .if(key, 'Value', '==', this.serializer(expectedValue))
              .then(this.client.put(key).value(newBuffer).ignoreLease())
              .commit();
      // proto3 leaves a false `succeeded` out of the answer, whatever etcd3's types say.
      const answer: { succeeded?: boolean } = result;
      return answer.succeeded === true;
    } catch (error) {
      Logger.getInstance().error(
        `Error in compareAndSet for key ${key}:`,
        error,
      );
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
