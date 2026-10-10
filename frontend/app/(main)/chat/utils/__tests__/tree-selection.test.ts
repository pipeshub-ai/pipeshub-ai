import { describe, expect, it } from 'vitest';
import type { ChatKnowledgeFilters } from '@/chat/types';
import {
  bucketFor,
  checkStateOf,
  hasAnyFilter,
  removeSelected,
  sameSelection,
  selectedIds,
  selectedNodeKind,
  toggleNode,
  type SelectableNode,
} from '../tree-selection';

// app-1 > group-1 > folder-1 > file-1, and folder-1 > sub-1 > file-2
const TREE: Record<string, string[]> = {
  'app-1': [],
  'group-1': ['app-1'],
  'folder-1': ['app-1', 'group-1'],
  'file-1': ['app-1', 'group-1', 'folder-1'],
  'sub-1': ['app-1', 'group-1', 'folder-1'],
  'file-2': ['app-1', 'group-1', 'folder-1', 'sub-1'],
  'kb-1': [],
};
const TYPES: Record<string, string> = {
  'app-1': 'app',
  'group-1': 'recordGroup',
  'folder-1': 'folder',
  'file-1': 'record',
  'sub-1': 'folder',
  'file-2': 'record',
  'kb-1': 'app',
};
const node = (id: string): SelectableNode => ({ id, nodeType: TYPES[id], ancestorIds: TREE[id] });
const ancestorsOf = (id: string) => TREE[id];
const empty: ChatKnowledgeFilters = { apps: [], kb: [], recordGroups: [], records: [] };
const pick = (filters: ChatKnowledgeFilters, id: string) => toggleNode(filters, node(id), ancestorsOf);
const state = (filters: ChatKnowledgeFilters, id: string) => checkStateOf(filters, node(id), ancestorsOf);

describe('bucketFor', () => {
  it('sends each kind of node under its own filter key', () => {
    expect(bucketFor('app')).toBe('apps');
    expect(bucketFor('recordGroup')).toBe('recordGroups');
    expect(bucketFor('folder')).toBe('records');
    expect(bucketFor('record')).toBe('records');
  });
});

describe('toggleNode', () => {
  it('selects a folder under records and a group under recordGroups', () => {
    expect(pick(empty, 'folder-1').records).toEqual(['folder-1']);
    expect(pick(empty, 'group-1').recordGroups).toEqual(['group-1']);
  });

  it('sends a collection under apps like any other app', () => {
    expect(pick(empty, 'kb-1')).toEqual({ ...empty, apps: ['kb-1'] });
  });

  it('unselects a selected node', () => {
    expect(pick(pick(empty, 'folder-1'), 'folder-1')).toEqual(empty);
  });

  it('unselects a collection that an older conversation stored under kb', () => {
    expect(pick({ ...empty, kb: ['kb-1'] }, 'kb-1')).toEqual(empty);
  });

  it('drops what was selected under a node when the node itself is selected', () => {
    const children = pick(pick(empty, 'file-1'), 'file-2');
    expect(children.records).toEqual(['file-1', 'file-2']);
    expect(pick(children, 'folder-1').records).toEqual(['folder-1']);
  });

  it('drops selections of every kind under a selected app', () => {
    const below = pick(pick(empty, 'group-1'), 'kb-1');
    expect(pick(below, 'app-1')).toEqual({ ...empty, apps: ['kb-1', 'app-1'] });
  });

  it('leaves a locked child alone', () => {
    const parent = pick(empty, 'folder-1');
    expect(pick(parent, 'file-2')).toBe(parent);
  });
});

describe('checkStateOf', () => {
  const filters = pick(empty, 'folder-1');

  it('marks the selected node checked', () => {
    expect(state(filters, 'folder-1')).toBe('checked');
  });

  it('marks everything under it inherited, at any depth', () => {
    expect(state(filters, 'file-1')).toBe('inherited');
    expect(state(filters, 'file-2')).toBe('inherited');
  });

  it('marks every ancestor indeterminate', () => {
    expect(state(filters, 'group-1')).toBe('indeterminate');
    expect(state(filters, 'app-1')).toBe('indeterminate');
  });

  it('leaves unrelated nodes unchecked', () => {
    expect(state(filters, 'kb-1')).toBe('unchecked');
  });

  it('treats a collection stored under kb as checked', () => {
    expect(state({ ...empty, kb: ['kb-1'] }, 'kb-1')).toBe('checked');
  });
});

describe('a collection among a saved agent\'s or a project\'s sources', () => {
  it('is kept under kb, apart from the apps', () => {
    const collection = { ...node('kb-1'), bucket: 'kb' as const };
    expect(toggleNode(empty, collection, ancestorsOf)).toEqual({ ...empty, kb: ['kb-1'] });
    expect(toggleNode({ ...empty, kb: ['kb-1'] }, collection, ancestorsOf)).toEqual(empty);
  });
});

describe('a record selected alone', () => {
  const alone = { ...empty, recordsExact: ['folder-1'] };

  it('shows as selected, without taking what is under it', () => {
    expect(state(alone, 'folder-1')).toBe('checked');
    expect(state(alone, 'file-1')).toBe('unchecked');
    expect(state(alone, 'group-1')).toBe('indeterminate');
  });

  it('is counted, listed and removed like any other selected node', () => {
    expect(hasAnyFilter(alone)).toBe(true);
    expect(selectedIds(alone)).toEqual(['folder-1']);
    expect(removeSelected(alone, 'folder-1')).toEqual({ ...empty, recordsExact: [] });
    expect(pick(alone, 'folder-1')).toEqual({ ...empty, recordsExact: [] });
  });

  it('is replaced when something above it is selected with its subtree', () => {
    expect(pick(alone, 'app-1')).toEqual({ ...empty, apps: ['app-1'], recordsExact: [] });
  });

  it('gets a file icon', () => {
    expect(selectedNodeKind('recordsExact', 'record')).toBe('file');
  });
});

describe('sameSelection', () => {
  it('compares key by key, in any order, with a missing key read as empty', () => {
    expect(sameSelection({ apps: ['a', 'b'], kb: [] }, { apps: ['b', 'a'], kb: [], records: [] })).toBe(true);
    expect(sameSelection({ apps: ['a'], kb: [] }, { apps: [], kb: ['a'] })).toBe(false);
    expect(sameSelection({ apps: [], kb: [], records: ['r'] }, { apps: [], kb: [] })).toBe(false);
  });
});

describe('removeSelected', () => {
  it('removes a node from whichever key holds it', () => {
    const filters = { apps: ['a'], kb: ['k'], recordGroups: ['g'], records: ['r', 'r2'] };
    expect(removeSelected(filters, 'r')).toEqual({ ...filters, records: ['r2'] });
    expect(removeSelected(filters, 'k')).toEqual({ ...filters, kb: [] });
  });

  it('leaves filters that only ever held apps and collections in that shape', () => {
    expect(removeSelected({ apps: ['a'], kb: ['k'] }, 'k')).toEqual({ apps: ['a'], kb: [] });
  });
});

describe('hasAnyFilter and selectedIds', () => {
  it('counts a selection made only of folders or groups', () => {
    expect(hasAnyFilter({ apps: [], kb: [], records: ['folder-1'] })).toBe(true);
    expect(hasAnyFilter({ apps: [], kb: [], recordGroups: ['group-1'] })).toBe(true);
  });

  it('is false for empty, missing and legacy two-key filters', () => {
    expect(hasAnyFilter(empty)).toBe(false);
    expect(hasAnyFilter({ apps: [], kb: [] })).toBe(false);
    expect(hasAnyFilter(undefined)).toBe(false);
  });

  it('reads the stored filters of a message the same way', () => {
    const stored = { id: 'folder-1', name: 'A', nodeType: 'folder', connector: 'KB' };
    expect(hasAnyFilter({ apps: [], kb: [], records: [stored] })).toBe(true);
    expect(hasAnyFilter({ apps: [], kb: [] })).toBe(false);
  });

  it('lists every selected id across the keys', () => {
    expect(selectedIds({ apps: ['a'], kb: ['k'], recordGroups: ['g'], records: ['r'] })).toEqual([
      'a',
      'k',
      'g',
      'r',
    ]);
  });
});
