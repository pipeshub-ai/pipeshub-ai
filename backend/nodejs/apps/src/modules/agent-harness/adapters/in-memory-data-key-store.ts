/* eslint-disable @typescript-eslint/require-await -- This deterministic adapter implements the asynchronous store port without I/O. */
import { WrappedDataKey } from '../domain/data-protection';
import { DataKeyStore } from '../ports/data-keys';

export class InMemoryDataKeyStore implements DataKeyStore {
  private readonly keys: WrappedDataKey[] = [];
  /** Seals counted per key id, as the key document's counter. */
  readonly seals = new Map<string, number>();
  /** Store reads, for tests of the key ring's cache. */
  reads = 0;

  async list(orgId: string): Promise<WrappedDataKey[]> {
    this.reads += 1;
    return structuredClone(this.keys.filter((key) => key.orgId === orgId));
  }

  async insert(key: WrappedDataKey): Promise<boolean> {
    if (
      this.keys.some(
        (stored) =>
          stored.kid === key.kid ||
          (stored.orgId === key.orgId && stored.version === key.version),
      )
    ) {
      return false;
    }
    this.keys.push(structuredClone(key));
    return true;
  }

  async reserveSeals(
    orgId: string,
    kid: string,
    count: number,
  ): Promise<number | null> {
    if (!this.keys.some((key) => key.orgId === orgId && key.kid === kid)) {
      return null;
    }
    const total = (this.seals.get(kid) ?? 0) + count;
    this.seals.set(kid, total);
    return total;
  }

  async deleteAll(orgId: string): Promise<number> {
    for (const key of this.keys) {
      if (key.orgId === orgId) this.seals.delete(key.kid);
    }
    const before = this.keys.length;
    const kept = this.keys.filter((key) => key.orgId !== orgId);
    this.keys.splice(0, this.keys.length, ...kept);
    return before - kept.length;
  }

  async orgIds(): Promise<string[]> {
    return [...new Set(this.keys.map((key) => key.orgId))].sort();
  }

  async replaceWrap(
    orgId: string,
    kid: string,
    expectedKekId: string,
    next: { kekId: string; wrapped: string },
  ): Promise<boolean> {
    const stored = this.keys.find(
      (key) =>
        key.orgId === orgId && key.kid === kid && key.kekId === expectedKekId,
    );
    if (stored === undefined) return false;
    stored.kekId = next.kekId;
    stored.wrapped = next.wrapped;
    return true;
  }
}
