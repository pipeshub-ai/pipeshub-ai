import { describe, expect, it } from 'vitest';
import type { KnowledgeHubNode } from '../../types';
import {
  isFolderLikeTableItem,
  isWebPathPlaceholder,
  shouldHideIndexingStatusForHubRecord,
} from '../kb-table-item-actions';

const baseNode: KnowledgeHubNode = {
  id: 'node-1',
  name: 'docs.pipeshub.com/ai-models/llm/',
  nodeType: 'record',
  parentId: null,
  origin: 'CONNECTOR',
  connector: 'WEB',
  hasChildren: true,
  permission: {
    role: 'READER',
    canEdit: false,
    canDelete: false,
  },
  sharingStatus: 'private',
  isInternal: true,
};

describe('web path placeholders', () => {
  it('treats internal WEB records with children as folder-like containers', () => {
    expect(isWebPathPlaceholder(baseNode)).toBe(true);
    expect(isFolderLikeTableItem(baseNode)).toBe(true);
    expect(shouldHideIndexingStatusForHubRecord(baseNode)).toBe(false);
  });

  it('keeps internal leaf WEB records hidden as structural records', () => {
    const leaf = { ...baseNode, hasChildren: false };

    expect(isWebPathPlaceholder(leaf)).toBe(false);
    expect(isFolderLikeTableItem(leaf)).toBe(false);
    expect(shouldHideIndexingStatusForHubRecord(leaf)).toBe(true);
  });

  it('does not treat internal records from other connectors as web path folders', () => {
    const nonWeb = { ...baseNode, connector: 'SLACK' };

    expect(isWebPathPlaceholder(nonWeb)).toBe(false);
    expect(isFolderLikeTableItem(nonWeb)).toBe(false);
    expect(shouldHideIndexingStatusForHubRecord(nonWeb)).toBe(true);
  });
});
