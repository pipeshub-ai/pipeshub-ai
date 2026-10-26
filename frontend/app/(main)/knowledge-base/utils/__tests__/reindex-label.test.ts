import assert from 'node:assert/strict';
import { describe, it } from 'vitest';
import { isRecordReindexBlocked } from '../reindex-label';

describe('isRecordReindexBlocked', () => {
  it('blocks GitHub and GitLab records', () => {
    assert.equal(isRecordReindexBlocked({ nodeType: 'record', connector: 'GITHUB' }), true);
    assert.equal(isRecordReindexBlocked({ nodeType: 'record', connector: 'GitLab Personal' }), true);
  });

  it('blocks folders under GitHub and GitLab code-repository trees', () => {
    assert.equal(isRecordReindexBlocked({ nodeType: 'folder', connector: 'GITHUB' }), true);
    assert.equal(isRecordReindexBlocked({ nodeType: 'folder', connector: 'GITHUB TEAMS' }), true);
    assert.equal(isRecordReindexBlocked({ nodeType: 'folder', connector: 'GITLAB' }), true);
    assert.equal(isRecordReindexBlocked({ nodeType: 'folder', connector: 'gitlab personal' }), true);
  });

  it('does not block Drive folders or the Code repository record group', () => {
    assert.equal(isRecordReindexBlocked({ nodeType: 'folder', connector: 'GOOGLE DRIVE' }), false);
    assert.equal(
      isRecordReindexBlocked({ nodeType: 'recordGroup', connector: 'GITHUB' }),
      false,
    );
  });
});
