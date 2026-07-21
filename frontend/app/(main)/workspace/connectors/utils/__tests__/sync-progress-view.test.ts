import { describe, expect, it } from 'vitest';
import {
  countByType,
  describeSyncProgress,
  isActiveConnectorSyncStatus,
  reconcileSyncActivity,
} from '../sync-progress-view';
import type { ConnectorSyncProgress, SyncProgressPhase } from '../../types';

function makeProgress(
  overrides: {
    isActive?: boolean;
    phase?: SyncProgressPhase;
    run?: Partial<ConnectorSyncProgress['run']>;
    coverage?: ConnectorSyncProgress['coverage'];
  } = {}
): ConnectorSyncProgress {
  const phase = overrides.phase ?? 'IDLE';
  return {
    connectorId: 'c1',
    isActive: overrides.isActive ?? false,
    phase,
    run: {
      runId: 'run-1',
      phase,
      discovered: 0,
      indexed: 0,
      failed: 0,
      skipped: 0,
      total: 0,
      processed: 0,
      percent: null,
      fullSync: false,
      startedAt: 0,
      heartbeatAt: 0,
      isStale: false,
      isActive: overrides.isActive ?? false,
      ...overrides.run,
    },
    coverage: overrides.coverage ?? {},
  };
}

describe('isActiveConnectorSyncStatus', () => {
  it('is true for SYNCING and FULL_SYNCING (any case)', () => {
    expect(isActiveConnectorSyncStatus('SYNCING')).toBe(true);
    expect(isActiveConnectorSyncStatus('full_syncing')).toBe(true);
  });

  it('is false for idle/absent statuses', () => {
    expect(isActiveConnectorSyncStatus('IDLE')).toBe(false);
    expect(isActiveConnectorSyncStatus(null)).toBe(false);
    expect(isActiveConnectorSyncStatus(undefined)).toBe(false);
  });
});

describe('describeSyncProgress', () => {
  it('shows indeterminate "Syncing source…" while discovering', () => {
    const view = describeSyncProgress(
      makeProgress({ isActive: true, phase: 'DISCOVERING', run: { discovered: 24 } })
    );
    expect(view.mode).toBe('discovering');
    if (view.mode === 'discovering') {
      expect(view.label).toBe('Syncing source…');
      expect(view.detail).toBe('24 queued');
    }
  });

  it('omits the queued count while discovering with nothing yet', () => {
    const view = describeSyncProgress(
      makeProgress({ isActive: true, phase: 'DISCOVERING', run: { discovered: 0 } })
    );
    expect(view.mode === 'discovering' && view.detail).toBe(null);
  });

  it('labels the discovering phase as a full sync when the status is FULL_SYNCING', () => {
    const view = describeSyncProgress(
      makeProgress({ isActive: true, phase: 'DISCOVERING', run: { discovered: 5 } }),
      'FULL_SYNCING'
    );
    expect(view.mode === 'discovering' && view.label).toBe('Full sync - syncing source…');
  });

  it('labels the discovering phase as a full sync when the run is a full sync', () => {
    const view = describeSyncProgress(
      makeProgress({ isActive: true, phase: 'DISCOVERING', run: { fullSync: true } })
    );
    expect(view.mode === 'discovering' && view.label).toBe('Full sync - syncing source…');
  });

  it('reports the deleting state regardless of run/coverage', () => {
    const view = describeSyncProgress(
      makeProgress({ isActive: true, phase: 'DISCOVERING', coverage: { total: 100, failed: 0 } }),
      'DELETING'
    );
    expect(view.mode).toBe('deleting');
    if (view.mode === 'deleting') {
      expect(view.label).toBe('Removing…');
    }
  });

  it('shows determinate "Indexing X of Y" during the indexing phase', () => {
    const view = describeSyncProgress(
      makeProgress({
        isActive: true,
        phase: 'INDEXING',
        run: { total: 42, processed: 18, percent: 43 },
      })
    );
    expect(view.mode).toBe('indexing');
    if (view.mode === 'indexing') {
      expect(view.label).toBe('Indexing 18 of 42');
      expect(view.percent).toBe(43);
    }
  });

  it('derives percent when the backend did not supply one', () => {
    const view = describeSyncProgress(
      makeProgress({
        isActive: true,
        phase: 'INDEXING',
        run: { total: 4, processed: 1, percent: null },
      })
    );
    expect(view.mode === 'indexing' && view.percent).toBe(25);
  });

  it('treats an active status with no run yet as discovering', () => {
    const view = describeSyncProgress(null, 'SYNCING');
    expect(view.mode).toBe('discovering');
  });

  it('falls back to lifetime coverage when idle', () => {
    const view = describeSyncProgress(
      makeProgress({ isActive: false, phase: 'IDLE', coverage: { total: 1240, failed: 0 } })
    );
    expect(view.mode).toBe('settled');
    if (view.mode === 'settled') {
      expect(view.label).toBe('Indexed');
      expect(view.hasErrors).toBe(false);
    }
  });

  it('flags failures in the settled coverage view', () => {
    const view = describeSyncProgress(
      makeProgress({ isActive: false, phase: 'IDLE', coverage: { total: 512, failed: 3 } })
    );
    expect(view.mode).toBe('settled');
    if (view.mode === 'settled') {
      expect(view.hasErrors).toBe(true);
      expect(view.failed).toBe(3);
    }
  });

  it('renders nothing when idle with no coverage', () => {
    expect(describeSyncProgress(null, 'IDLE').mode).toBe('none');
    expect(describeSyncProgress(makeProgress({ coverage: { total: 0 } })).mode).toBe('none');
  });
});

describe('reconcileSyncActivity', () => {
  const instances = [
    { id: 'a', type: 'Google Drive', status: 'SYNCING' },
    { id: 'b', type: 'Google Drive', status: 'IDLE' },
    { id: 'c', type: 'Confluence', status: 'IDLE' },
  ];

  it('tracks status-syncing instances even if not previously tracked', () => {
    const { activeIdToType, trackedIds } = reconcileSyncActivity(instances, {}, []);
    expect(activeIdToType).toEqual({ a: 'Google Drive' });
    expect(trackedIds).toEqual(['a']);
  });

  it('keeps a tracked instance active through the indexing phase (status idle, run active)', () => {
    // 'b' finished discovery (status IDLE) but its run is still active.
    const { activeIdToType, trackedIds } = reconcileSyncActivity(
      instances,
      { b: true },
      ['b']
    );
    expect(activeIdToType).toEqual({ a: 'Google Drive', b: 'Google Drive' });
    expect(new Set(trackedIds)).toEqual(new Set(['a', 'b']));
  });

  it('drops settled instances (run inactive, status idle)', () => {
    const { activeIdToType, trackedIds } = reconcileSyncActivity(
      instances,
      { b: false },
      ['b']
    );
    expect(activeIdToType).toEqual({ a: 'Google Drive' });
    expect(trackedIds).toEqual(['a']);
  });

  it('drops tracked ids that no longer exist', () => {
    const { activeIdToType, trackedIds } = reconcileSyncActivity(
      instances,
      { gone: true },
      ['gone']
    );
    expect(activeIdToType).toEqual({ a: 'Google Drive' });
    expect(trackedIds).toEqual(['a']);
  });
});

describe('countByType', () => {
  it('aggregates active instances per connector type', () => {
    expect(
      countByType({ a: 'Google Drive', b: 'Google Drive', c: 'Confluence' })
    ).toEqual({ 'Google Drive': 2, Confluence: 1 });
  });

  it('returns an empty map when nothing is active', () => {
    expect(countByType({})).toEqual({});
  });
});
