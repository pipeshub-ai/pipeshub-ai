import type { EtcdAuthAdmin } from './etcd-auth';
export interface DistributedKeyValueStore<T> {
  createKey(key: string, value: T): Promise<void>;
  updateValue(key: string, value: T): Promise<void>;
  getKey(key: string): Promise<T | null>;
  deleteKey(key: string): Promise<void>;
  getAllKeys(): Promise<string[]>;
  watchKey(key: string, callback: (value: T | null) => void): Promise<void>;
  listKeysInDirectory(directory: string): Promise<string[]>;
  compareAndSet(key: string, expectedValue: T | null, newValue: T): Promise<boolean>;
  healthCheck(): Promise<boolean>;
  publishCacheInvalidation(key: string): Promise<void>;
  /** etcd only (R38IDENT-FU-9): its role-based auth, as this process's certificate's user. */
  authAdmin?(): EtcdAuthAdmin | null;
}
