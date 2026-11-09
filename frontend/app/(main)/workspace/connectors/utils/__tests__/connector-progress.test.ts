import { describe, it, expect } from 'vitest';
import type { TFunction } from 'i18next';
import {
  describeConnectorProgress,
  etaLabel,
  nextPollMs,
  type ConnectorProgress,
  type SyncRun,
} from '../connector-progress';

// Echoes the key and its params, so tests read which sentence was chosen.
const t = ((key: string, params?: Record<string, unknown>) =>
  params ? `${key.replace('workspace.connectors.progress.', '')} ${JSON.stringify(params)}` : key.replace('workspace.connectors.progress.', '')) as unknown as TFunction;

const NOW = 1_800_000_000_000;

const counts = { found: 0, new: 0, changed: 0, unchanged: 0, removed: 0, filtered: 0, containers: 0 };

function run(partial: Partial<SyncRun> = {}): SyncRun {
  return { runId: 'r1', trigger: 'manual', full: false, startedAt: NOW - 60_000, counts, ...partial };
}

function view(partial: Partial<ConnectorProgress> = {}): ConnectorProgress {
  return {
    connectorId: 'c1',
    status: 'IDLE',
    isActive: true,
    run: null,
    lastRun: null,
    lastError: null,
    awaitingFirstSync: false,
    indexing: { waiting: 0, inProgress: 0, failed: 0, indexedRecently: 0, perMinute: 0, etaSeconds: null, waitingInLine: false },
    ...partial,
  };
}

const describeIt = (v: ConnectorProgress) => describeConnectorProgress(t, v, 'Drive', NOW);

describe('describeConnectorProgress', () => {
  it('shows a bar only when the total is really known', () => {
    const withUnits = describeIt(view({ status: 'SYNCING', run: run({ unit: { kind: 'mailbox', done: 3, total: 12 } }) }));
    expect(withUnits.percent).toBe(25);
    const unknown = describeIt(view({ status: 'SYNCING', run: run({ phase: 'content', counts: { ...counts, found: 40 } }) }));
    expect(unknown.percent).toBeNull();
    expect(unknown.moving).toBe(true);
    expect(unknown.text).toContain('run.content');
  });

  it('never claims 100% while a sync is still running', () => {
    const line = describeIt(view({ status: 'SYNCING', run: run({ expectedTotal: 10, counts: { ...counts, found: 12 } }) }));
    expect(line.percent).toBe(99);
  });

  it('puts a rate-limit pause ahead of everything else in a run', () => {
    const line = describeIt(view({ status: 'SYNCING', run: run({ throttledUntil: NOW + 60_000, stalled: true }) }));
    expect(line.tone).toBe('warn');
    expect(line.text).toContain('run.throttled');
  });

  it('says plainly when a run is making no progress', () => {
    const line = describeIt(view({ status: 'SYNCING', run: run({ stalled: true, lastActivityAt: NOW - 25 * 60_000 }) }));
    expect(line.text).toContain('run.stalled');
    expect(line.text).toContain('"n":25');
  });

  it('reports a failed last sync with its reason', () => {
    const line = describeIt(view({ lastRun: run({ outcome: 'failed', finishedAt: NOW, error: { code: 'auth' } }) }));
    expect(line.tone).toBe('error');
    expect(line.text).toContain('error.auth.title');
  });

  it('never calls a stopped sync up to date, and keeps a failure from before the stop', () => {
    const stopped = describeIt(view({ lastRun: run({ outcome: 'stopped', finishedAt: NOW }) }));
    expect(stopped.tone).toBe('warn');
    expect(stopped.text).toBe('stopped');
    const afterFailure = describeIt(
      view({ lastRun: run({ outcome: 'stopped', finishedAt: NOW }), lastError: { code: 'auth' } }),
    );
    expect(afterFailure.tone).toBe('error');
    expect(afterFailure.text).toContain('stoppedAfterFailure');
    expect(afterFailure.text).toContain('error.auth.title');
  });

  it('shows indexing still draining after the sync ended, with an estimate when known', () => {
    const line = describeIt(
      view({
        lastRun: run({ outcome: 'succeeded', finishedAt: NOW }),
        indexing: { waiting: 90, inProgress: 10, failed: 0, indexedRecently: 100, perMinute: 10, etaSeconds: 600, waitingInLine: false },
      }),
    );
    expect(line.text).toContain('indexing.leftWithEta');
  });

  it('tells work stuck behind other work apart from work moving slowly', () => {
    const line = describeIt(
      view({
        lastRun: run({ outcome: 'succeeded', finishedAt: NOW }),
        indexing: { waiting: 5, inProgress: 0, failed: 0, indexedRecently: 0, perMinute: 0, etaSeconds: null, waitingInLine: true },
      }),
    );
    expect(line.text).toContain('indexing.inLine');
  });

  it('is up to date, and says how many items could not be indexed', () => {
    const clean = describeIt(view({ lastRun: run({ outcome: 'succeeded', finishedAt: NOW }) }));
    expect(clean.tone).toBe('ok');
    const withFailures = describeIt(
      view({
        lastRun: run({ outcome: 'succeeded', finishedAt: NOW }),
        indexing: { waiting: 0, inProgress: 0, failed: 3, indexedRecently: 0, perMinute: 0, etaSeconds: null, waitingInLine: false },
      }),
    );
    expect(withFailures.tone).toBe('warn');
    expect(withFailures.text).toContain('withFailures');
  });

  it('handles a connector that is off, being removed, queued or never synced', () => {
    expect(describeIt(view({ isActive: false })).tone).toBe('off');
    expect(describeIt(view({ status: 'DELETING' })).text).toBe('removing');
    expect(describeIt(view({ status: 'QUEUED' })).text).toBe('queued');
    expect(describeIt(view()).text).toBe('notSyncedYet');
  });

  it('does not call content synced before history was kept "not synced yet"', () => {
    const line = describeIt(view({ hasSearchableContent: true }));
    expect(line.text).toBe('noSyncRecorded');
    expect(line.tone).toBe('ok');
  });
});

describe('etaLabel', () => {
  it('rounds coarsely, since indexing speed varies', () => {
    expect(etaLabel(t, 30)).toBe('eta.underMinute');
    expect(etaLabel(t, 7 * 60)).toContain('"n":7');
    expect(etaLabel(t, 23 * 60)).toContain('"n":25');
    expect(etaLabel(t, 3 * 3600)).toContain('eta.hours');
    expect(etaLabel(t, 10 * 3600)).toBe('eta.longer');
  });
});

describe('nextPollMs', () => {
  it('polls often while syncing, slowly while indexing drains, and not at rest', () => {
    expect(nextPollMs(view({ status: 'SYNCING', run: run() }))).toBe(5_000);
    expect(
      nextPollMs(view({ indexing: { waiting: 4, inProgress: 0, failed: 0, indexedRecently: 0, perMinute: 0, etaSeconds: null, waitingInLine: true } })),
    ).toBe(30_000);
    expect(nextPollMs(view())).toBeNull();
  });
});
