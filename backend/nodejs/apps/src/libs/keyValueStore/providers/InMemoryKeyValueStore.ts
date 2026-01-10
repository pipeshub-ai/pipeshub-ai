import { DistributedKeyValueStore } from '../keyValueStore';
import { EtcdOperationNotSupportedError, KeyAlreadyExistsError, KeyNotFoundError } from '../../errors/etcd.errors';

export class InMemoryKeyValueStore<T> implements DistributedKeyValueStore<T> {
  private store: Map<string, T> = new Map();

  async createKey(key: string, value: T): Promise<void> {
    if (this.store.has(key)) {
      throw new KeyAlreadyExistsError(`Key "${key}" already exists.`);
    }
    this.store.set(key, value);
  }

  async updateValue(key: string, value: T): Promise<void> {
    if (!this.store.has(key)) {
      throw new KeyNotFoundError(`Key "${key}" does not exist.`);
    }
    this.store.set(key, value);
  }

  async getKey(key: string): Promise<T | null> {
    return this.store.get(key) ?? null;
  }

  async deleteKey(key: string): Promise<void> {
    this.store.delete(key);
  }

  async getAllKeys(): Promise<string[]> {
    return Array.from(this.store.keys());
  }

  async watchKey(
    _key: string,
    _callback: (value: T | null) => void,
  ): Promise<void> {
    // In-memory implementation does not support watching keys.
    throw new EtcdOperationNotSupportedError('Watch operation is not supported in the in-memory store.');
  }

  async listKeysInDirectory(directory: string): Promise<string[]> {
    const keys = Array.from(this.store.keys());
    return keys.filter((key) => key.startsWith(directory));
  }

  async compareAndSet(key: string, expectedValue: T | null, newValue: T): Promise<boolean> {
    const currentValue = this.store.get(key) ?? null;
    
    // Compare current value with expected value
    // For null comparison, check if both are null
    if (expectedValue === null && currentValue === null) {
      this.store.set(key, newValue);
      return true;
    }
    
    // For non-null values, perform comparison
    // Use JSON.stringify for deep equality of objects/arrays, 
    // or strict equality for primitives
    let valuesMatch = false;
    if (currentValue === expectedValue) {
      // Strict equality works for primitives and same object references
      valuesMatch = true;
    } else if (
      typeof currentValue === 'object' &&
      typeof expectedValue === 'object' &&
      currentValue !== null &&
      expectedValue !== null
    ) {
      // Deep equality check for objects using JSON.stringify
      try {
        valuesMatch = JSON.stringify(currentValue) === JSON.stringify(expectedValue);
      } catch {
        // If JSON.stringify fails, fall back to strict equality
        valuesMatch = false;
      }
    }
    
    if (valuesMatch) {
      this.store.set(key, newValue);
      return true;
    }
    
    // Values don't match, CAS failed
    return false;
  }
}
