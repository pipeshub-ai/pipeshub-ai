import { describe, expect, it } from 'vitest';
import { indexingReasonHint } from '../indexing-reason-hint';

describe('indexingReasonHint', () => {
  it('shows a completed record reason as a note', () => {
    expect(indexingReasonHint('COMPLETED', 'Only the first and last part of this file is indexed.')).toBe('note');
  });

  it.each(['FAILED', 'AUTO_INDEX_OFF', 'FILE_TYPE_NOT_SUPPORTED', 'PASSWORD_PROTECTED', 'NOT_STARTED'])(
    'keeps showing the %s reason as before',
    (status) => {
      expect(indexingReasonHint(status, 'why')).toBe('reason');
    },
  );

  it('shows nothing without a reason or for an in-progress record', () => {
    expect(indexingReasonHint('COMPLETED', '  ')).toBeNull();
    expect(indexingReasonHint('COMPLETED', undefined)).toBeNull();
    expect(indexingReasonHint('IN_PROGRESS', 'why')).toBeNull();
  });
});
