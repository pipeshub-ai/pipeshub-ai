import { describe, it, expect } from 'vitest';
import { describeSyncConflict } from '../sync-conflict-copy';

describe('describeSyncConflict', () => {
  it('re-running a quick sync offers a plain restart', () => {
    const copy = describeSyncConflict('SYNCING', false);
    expect(copy.title).toBe('Sync already running');
    expect(copy.confirmLabel).toBe('Restart sync');
    expect(copy.cancelLabel).toBe('Keep current sync');
  });

  it('re-running a full sync offers a full-sync restart', () => {
    const copy = describeSyncConflict('FULL_SYNCING', true);
    expect(copy.title).toBe('Full sync already running');
    expect(copy.confirmLabel).toBe('Restart full sync');
  });

  it('escalates a running quick sync to a full sync', () => {
    const copy = describeSyncConflict('SYNCING', true);
    expect(copy.title).toBe('A sync is running');
    expect(copy.confirmLabel).toBe('Cancel & full sync');
  });

  it('discourages downgrading a running full sync to a quick sync', () => {
    const copy = describeSyncConflict('FULL_SYNCING', false);
    expect(copy.title).toBe('Full sync in progress');
    expect(copy.confirmLabel).toBe('Cancel & sync');
    expect(copy.cancelLabel).toBe('Keep full sync');
  });

  it('is case-insensitive and defaults unknown status to the quick-sync copy', () => {
    expect(describeSyncConflict('full_syncing', true).title).toBe(
      'Full sync already running'
    );
    expect(describeSyncConflict(undefined, false).title).toBe('Sync already running');
    expect(describeSyncConflict('IDLE', false).title).toBe('Sync already running');
  });
});
