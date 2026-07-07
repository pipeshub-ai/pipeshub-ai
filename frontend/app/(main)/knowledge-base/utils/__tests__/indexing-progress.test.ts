import { describe, it, expect } from 'vitest';
import {
  extractionDurationMs,
  formatProgressDetail,
  getIndexingProgressView,
  hasActiveIndexing,
  isActiveIndexingStatus,
} from '../indexing-progress';

const NOW = 1_000_000_000_000;
const MB = 1024 * 1024;

describe('isActiveIndexingStatus', () => {
  it('treats QUEUED and IN_PROGRESS as active', () => {
    expect(isActiveIndexingStatus('QUEUED')).toBe(true);
    expect(isActiveIndexingStatus('IN_PROGRESS')).toBe(true);
  });

  it('treats terminal statuses as inactive', () => {
    for (const s of ['COMPLETED', 'FAILED', 'EMPTY', 'AUTO_INDEX_OFF', 'NOT_STARTED', 'FILE_TYPE_NOT_SUPPORTED']) {
      expect(isActiveIndexingStatus(s)).toBe(false);
    }
    expect(isActiveIndexingStatus(null)).toBe(false);
    expect(isActiveIndexingStatus(undefined)).toBe(false);
  });
});

describe('hasActiveIndexing', () => {
  it('returns true when any record is in flight', () => {
    expect(
      hasActiveIndexing([
        { indexingStatus: 'COMPLETED' },
        { indexingStatus: 'IN_PROGRESS' },
      ]),
    ).toBe(true);
  });

  it('returns false when every record is settled', () => {
    expect(
      hasActiveIndexing([
        { indexingStatus: 'COMPLETED' },
        { indexingStatus: 'FAILED' },
        { indexingStatus: null },
      ]),
    ).toBe(false);
  });

  it('returns false for an empty list', () => {
    expect(hasActiveIndexing([])).toBe(false);
  });
});

describe('extractionDurationMs', () => {
  it('scales with file size', () => {
    const small = extractionDurationMs({ sizeInBytes: 1 * MB, extension: 'pdf' });
    const large = extractionDurationMs({ sizeInBytes: 20 * MB, extension: 'pdf' });
    expect(large).toBeGreaterThan(small);
  });

  it('charges OCR-heavy types more per byte than plain text', () => {
    const pdf = extractionDurationMs({ sizeInBytes: 10 * MB, extension: 'pdf' });
    const office = extractionDurationMs({ sizeInBytes: 10 * MB, extension: 'docx' });
    const text = extractionDurationMs({ sizeInBytes: 10 * MB, extension: 'txt' });
    expect(pdf).toBeGreaterThan(office);
    expect(office).toBeGreaterThan(text);
  });

  it('classifies by mime type when extension is absent', () => {
    const pdf = extractionDurationMs({ sizeInBytes: 10 * MB, mimeType: 'application/pdf' });
    const image = extractionDurationMs({ sizeInBytes: 10 * MB, mimeType: 'image/png' });
    const text = extractionDurationMs({ sizeInBytes: 10 * MB, mimeType: 'text/plain' });
    expect(pdf).toEqual(image);
    expect(pdf).toBeGreaterThan(text);
  });

  it('uses a 30-minute floor for PDFs regardless of small file size', () => {
    const harryPotterSized = extractionDurationMs({
      sizeInBytes: 766_585,
      extension: 'pdf',
    });
    expect(harryPotterSized).toBeGreaterThanOrEqual(30 * 60 * 1000);
  });
});

describe('getIndexingProgressView', () => {
  it('reports QUEUED records as active with a queued label', () => {
    const view = getIndexingProgressView({ indexingStatus: 'QUEUED' }, NOW);
    expect(view.isActive).toBe(true);
    expect(view.label).toBe('Queued');
    expect(view.stepIndex).toBe(0);
  });

  it('uses contiguous bands with no dead zone between queued and extracting', () => {
    const queued = getIndexingProgressView({ indexingStatus: 'QUEUED' }, NOW);
    const extractingStart = getIndexingProgressView(
      { indexingStatus: 'IN_PROGRESS', indexingStage: 'EXTRACTING', lastActivityTimestamp: NOW },
      NOW,
    );
    // Extraction begins exactly where queued sits — no jump/gap.
    expect(extractingStart.percent).toBe(queued.percent);
  });

  it('labels the EXTRACTING stage while in progress', () => {
    const view = getIndexingProgressView(
      { indexingStatus: 'IN_PROGRESS', indexingStage: 'EXTRACTING', lastActivityTimestamp: NOW },
      NOW,
    );
    expect(view.label).toBe('Extracting content');
    expect(view.percent).toBeGreaterThan(0);
    expect(view.percent).toBeLessThan(100);
  });

  it('estimates extraction progress from elapsed time, sized by the input file', () => {
    const record = {
      indexingStatus: 'IN_PROGRESS' as const,
      indexingStage: 'EXTRACTING' as const,
      lastActivityTimestamp: NOW,
      sizeInBytes: 5 * MB,
      extension: 'pdf',
    };
    const duration = extractionDurationMs(record);
    const initial = getIndexingProgressView(record, NOW);
    const later = getIndexingProgressView(record, NOW + duration / 2);
    expect(later.percent).toBeGreaterThan(initial.percent);
    expect(later.percent).toBeLessThan(65);
    expect(later.isEstimated).toBe(true);
    expect(later.detail).toBeUndefined();
  });

  it('prefers real extraction metrics over the size estimate', () => {
    const view = getIndexingProgressView(
      {
        indexingStatus: 'IN_PROGRESS',
        indexingStage: 'EXTRACTING',
        lastActivityTimestamp: NOW,
        sizeInBytes: 50 * MB,
        extension: 'pdf',
        indexingProgress: {
          current: 4,
          total: 8,
          unit: 'pages',
          phase: 'extracting',
          message: 'Extracted 4 of 8 pages',
        },
      },
      NOW + 5_000,
    );
    // 5 → 65 band, halfway = 35, and it is not an estimate.
    expect(view.percent).toBe(35);
    expect(view.isEstimated).toBeFalsy();
    expect(view.detail).toBe('Extracting page 4 of 8');
  });

  it('advances faster for smaller files of the same type', () => {
    const base = {
      indexingStatus: 'IN_PROGRESS' as const,
      indexingStage: 'EXTRACTING' as const,
      lastActivityTimestamp: NOW,
      extension: 'pdf',
    };
    const at = NOW + 30_000;
    const smallView = getIndexingProgressView({ ...base, sizeInBytes: 1 * MB }, at);
    const largeView = getIndexingProgressView({ ...base, sizeInBytes: 50 * MB }, at);
    expect(smallView.percent).toBeGreaterThan(largeView.percent);
  });

  it('advances percent from EXTRACTING to INDEXING', () => {
    const extracting = getIndexingProgressView(
      { indexingStatus: 'IN_PROGRESS', indexingStage: 'EXTRACTING', lastActivityTimestamp: NOW },
      NOW,
    );
    const indexing = getIndexingProgressView(
      { indexingStatus: 'IN_PROGRESS', indexingStage: 'INDEXING', lastActivityTimestamp: NOW },
      NOW,
    );
    expect(indexing.percent).toBeGreaterThan(extracting.percent);
    expect(indexing.label).toBe('Indexing');
  });

  it('uses substage metrics to calculate indexing progress within the indexing band', () => {
    const view = getIndexingProgressView(
      {
        indexingStatus: 'IN_PROGRESS',
        indexingStage: 'INDEXING',
        lastActivityTimestamp: NOW,
        indexingProgress: {
          current: 5,
          total: 10,
          unit: 'chunks',
          phase: 'embedding',
          message: 'Embedding chunk 5 of 10',
        },
      },
      NOW,
    );
    // 65 → 98 band, halfway ≈ 82.
    expect(view.percent).toBe(82);
    expect(view.detail).toBe('Embedding chunk 5 of 10');
    expect(view.isEstimated).toBeFalsy();
    expect(view.stepIndex).toBe(2);
  });

  it('rewrites legacy backend "documents" copy as chunks for a single uploaded file', () => {
    expect(
      formatProgressDetail('INDEXING', {
        current: 200,
        total: 277,
        unit: 'documents',
        phase: 'embedding',
        message: 'Embedded 200 of 277 documents',
      }),
    ).toBe('Embedding chunk 200 of 277');
  });

  it('formats page-level extraction copy', () => {
    expect(
      formatProgressDetail('EXTRACTING', {
        current: 3,
        total: 12,
        unit: 'pages',
        phase: 'extracting',
      }),
    ).toBe('Extracting page 3 of 12');
  });

  it('holds at the indexing stage base when metrics are missing, never estimating', () => {
    const initial = getIndexingProgressView(
      { indexingStatus: 'IN_PROGRESS', indexingStage: 'INDEXING', lastActivityTimestamp: NOW },
      NOW,
    );
    const later = getIndexingProgressView(
      { indexingStatus: 'IN_PROGRESS', indexingStage: 'INDEXING', lastActivityTimestamp: NOW },
      NOW + 60_000,
    );
    expect(initial.percent).toBe(65);
    expect(later.percent).toBe(65);
    expect(later.isEstimated).toBeFalsy();
  });

  it('falls back to a generic label when stage is missing (any file type/model)', () => {
    const view = getIndexingProgressView(
      { indexingStatus: 'IN_PROGRESS', indexingStage: null, lastActivityTimestamp: NOW },
      NOW,
    );
    expect(view.label).toBe('Processing');
    expect(view.isActive).toBe(true);
  });

  it('never reports a stalled state, even with an old heartbeat', () => {
    const view = getIndexingProgressView(
      {
        indexingStatus: 'IN_PROGRESS',
        indexingStage: 'EXTRACTING',
        lastActivityTimestamp: NOW - 60 * 60 * 1000,
        sizeInBytes: 766_585,
        extension: 'pdf',
      },
      NOW,
    );
    expect(view.isActive).toBe(true);
    expect(view.label).toBe('Extracting content');
    // Asymptotic estimate keeps moving on long opaque parses; never hits band end.
    expect(view.percent).toBeGreaterThan(5);
    expect(view.percent).toBeLessThan(65);
    expect('isStalled' in view).toBe(false);
  });

  it('keeps extraction estimate moving through a 30-minute PDF parse', () => {
    const record = {
      indexingStatus: 'IN_PROGRESS' as const,
      indexingStage: 'EXTRACTING' as const,
      lastActivityTimestamp: NOW,
      sizeInBytes: 766_585,
      extension: 'pdf',
    };
    const at15 = getIndexingProgressView(record, NOW + 15 * 60 * 1000);
    const at30 = getIndexingProgressView(record, NOW + 30 * 60 * 1000);
    expect(at15.percent).toBeGreaterThan(5);
    expect(at30.percent).toBeGreaterThan(at15.percent);
    expect(at30.percent).toBeLessThan(65);
  });

  it('ignores a stale stage on terminal records', () => {
    const view = getIndexingProgressView(
      { indexingStatus: 'COMPLETED', indexingStage: 'EXTRACTING', lastActivityTimestamp: NOW - 10_000 },
      NOW,
    );
    expect(view.isActive).toBe(false);
    expect(view.percent).toBe(100);
  });
});
