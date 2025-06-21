export interface DistributedKeyValueStore<T> {
  createKey(key: string, value: T): Promise<void>;
  updateValue(key: string, value: T): Promise<void>;
  getKey(key: string): Promise<T | null>;
  deleteKey(key: string): Promise<void>;
  getAllKeys(): Promise<string[]>;
  watchKey(key: string, callback: (value: T | null) => void): Promise<void>;
  listKeysInDirectory(directory: string): Promise<string[]>;
}
