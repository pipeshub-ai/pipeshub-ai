import assert from 'node:assert/strict';
import { describe, expect, it } from 'vitest';
import type { TFunction } from 'i18next';
import { getIndexStatusIcon } from '@/lib/utils/index-status-icon';
import en from '@/lib/i18n/locales/en-US.json';
import { getKbItemStatusLabel } from '../../components/kb-list-view';
import type { KnowledgeHubNode } from '../../types';
import { parseAllRecordsParams, parseCollectionsParams, serializeAllRecordsParams } from '../../url-params';
import {
  getReindexAction,
  getReindexMenuOptions,
  getReindexNodeFromHubItem,
} from '../reindex-label';

const REASON = 'This file is password-protected, so we can\'t read it. Remove the password, then upload it again.';

// The real English strings, so the label test checks what a person reads.
const t = ((key: string) =>
  key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], en)) as unknown as TFunction;

function hubRecord(overrides: Partial<KnowledgeHubNode> = {}): KnowledgeHubNode {
  return {
    id: 'r1',
    name: 'salaries.pdf',
    nodeType: 'record',
    origin: 'COLLECTION',
    connector: 'KB',
    recordType: 'FILE',
    indexingStatus: 'PASSWORD_PROTECTED',
    reason: REASON,
    hasChildren: false,
    ...overrides,
  } as KnowledgeHubNode;
}

describe('PASSWORD_PROTECTED in the collections list', () => {
  it('reads as its own status, with the reason', () => {
    const label = getKbItemStatusLabel(hubRecord(), t);

    assert.ok(label.startsWith('Password protected - '), label);
    assert.ok(label.includes('Remove the password'), label);
  });

  it('is not labelled Failed or as an unknown status', () => {
    const label = getKbItemStatusLabel(hubRecord({ reason: null }), t);

    assert.equal(label, 'Password protected');
  });

  it('has a lock icon', () => {
    assert.equal(getIndexStatusIcon('PASSWORD_PROTECTED'), 'lock');
  });
});

describe('PASSWORD_PROTECTED in the URL', () => {
  it('survives a filter round trip', () => {
    const params = serializeAllRecordsParams(
      { indexingStatus: ['PASSWORD_PROTECTED'] },
      { field: 'updatedAt', order: 'desc' },
      { cursor: null, limit: 50 },
      '',
    );
    const parsed = parseAllRecordsParams(new URLSearchParams(params));

    assert.deepEqual(parsed.filter.indexingStatus, ['PASSWORD_PROTECTED']);
    assert.deepEqual(
      parseCollectionsParams(new URLSearchParams({ indexingStatus: 'PASSWORD_PROTECTED' })).filter.indexingStatus,
      ['PASSWORD_PROTECTED'],
    );
  });
});

describe('PASSWORD_PROTECTED and reindexing', () => {
  it('a single locked record can be retried (a synced file is read again from its source)', () => {
    assert.equal(getReindexAction(getReindexNodeFromHubItem({ nodeType: 'record', indexingStatus: 'PASSWORD_PROTECTED' })), 'retry-indexing');
  });

  it('"Reindex failed" does not select it', () => {
    const options = getReindexMenuOptions(
      getReindexNodeFromHubItem({ nodeType: 'folder', indexingStatus: 'COMPLETED', hasChildren: true, connector: 'GOOGLE DRIVE' }),
    );
    const failed = options.find((o) => o.labelKey === 'menu.reindexFailed');

    expect(failed?.statusFilters).toEqual(['FAILED']);
  });
});
