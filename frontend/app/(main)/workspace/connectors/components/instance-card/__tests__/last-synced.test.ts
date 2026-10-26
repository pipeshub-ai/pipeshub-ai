import { describe, it, expect } from 'vitest';
import { lastSyncedAt } from '../utils';

describe('lastSyncedAt', () => {
  it('is when the last sync run finished, not when the connector was last written', () => {
    expect(lastSyncedAt({ lastSyncFinishedAtTimestamp: 100, updatedAtTimestamp: 900 })).toBe(100);
  });

  it('falls back to the last write for a connector that has not finished a run since upgrading', () => {
    expect(lastSyncedAt({ updatedAtTimestamp: 900 })).toBe(900);
    expect(lastSyncedAt({ lastSyncFinishedAtTimestamp: null, updatedAtTimestamp: 900 })).toBe(900);
  });

  it('is undefined for a connector that never synced', () => {
    expect(lastSyncedAt({})).toBeUndefined();
  });
});
