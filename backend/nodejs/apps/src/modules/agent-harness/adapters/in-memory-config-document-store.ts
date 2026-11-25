/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous store port without I/O. */
import {
  ConfigDocument,
  ConfigDocumentStore,
} from '../ports/config-document-store';

export class InMemoryConfigDocumentStore<T> implements ConfigDocumentStore<T> {
  private value: T | null = null;
  private version = 0;
  /** Number of successful writes, for tests that check convergence. */
  writes = 0;

  async read(): Promise<ConfigDocument<T>> {
    return {
      value: this.value === null ? null : structuredClone(this.value),
      revision: this.value === null ? null : String(this.version),
    };
  }

  async replace(expected: string | null, value: T): Promise<boolean> {
    const current = this.value === null ? null : String(this.version);
    if (current !== expected) return false;
    this.value = structuredClone(value);
    this.version += 1;
    this.writes += 1;
    return true;
  }
}
