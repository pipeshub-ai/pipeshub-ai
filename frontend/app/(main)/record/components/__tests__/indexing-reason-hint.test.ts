import { describe, expect, it } from 'vitest';
import { indexingStatusHint } from '../indexing-reason-hint';

const NOTE = 'Only the first and last part of this file is indexed (file is larger than 256 KiB).';

describe('indexingStatusHint', () => {
  it('shows the indexing note of a completed record', () => {
    expect(indexingStatusHint({ indexingStatus: 'COMPLETED', indexingNote: NOTE, reason: 'nothing to classify' }))
      .toEqual({ kind: 'note', content: NOTE });
  });

  it('shows nothing for a completed record whose later stage left a reason', () => {
    expect(indexingStatusHint({ indexingStatus: 'COMPLETED', reason: 'Extraction skipped: LLM not configured' }))
      .toBeNull();
    expect(indexingStatusHint({ indexingStatus: 'COMPLETED', reason: 'nothing to classify', indexingNote: null }))
      .toBeNull();
  });

  it.each(['FAILED', 'AUTO_INDEX_OFF', 'FILE_TYPE_NOT_SUPPORTED', 'PASSWORD_PROTECTED', 'NOT_STARTED'])(
    'keeps showing the %s reason as before',
    (status) => {
      expect(indexingStatusHint({ indexingStatus: status, reason: 'why', indexingNote: NOTE }))
        .toEqual({ kind: 'reason', content: 'why' });
    },
  );

  it('shows nothing without a reason or note, or for a record still in progress', () => {
    expect(indexingStatusHint({ indexingStatus: 'COMPLETED', indexingNote: '  ' })).toBeNull();
    expect(indexingStatusHint({ indexingStatus: 'FAILED', reason: ' ' })).toBeNull();
    expect(indexingStatusHint({ indexingStatus: 'IN_PROGRESS', reason: 'why', indexingNote: NOTE })).toBeNull();
  });
});
