import { beforeEach, describe, expect, it } from 'vitest';
import { useChatStore } from '@/chat/store';
import type { AppliedFilters } from '@/chat/types';
import { appliedFiltersFromNodes, filtersFromAppliedFilters, restoreAppliedFilters } from '../restore-applied-filters';

const node = (id: string, name: string, nodeType: string, connector: string) => ({ id, name, nodeType, connector });

const APPLIED: AppliedFilters = {
  apps: [node('jira-1', 'Jira', 'app', 'Jira'), node('alpha', 'GS-Alpha', 'app', 'KB')],
  kb: [],
  recordGroups: [node('group-1', 'Project PT', 'recordGroup', 'Jira')],
  records: [node('folder-1', 'A', 'folder', 'KB'), node('file-1', 'b1.md', 'record', 'KB')],
};

beforeEach(() => {
  useChatStore.getState().setFilters({ apps: [], kb: [] });
  useChatStore.getState().setAgentKnowledgeScope(null);
});

describe('filtersFromAppliedFilters', () => {
  it('brings back every kind of selected node', () => {
    expect(filtersFromAppliedFilters(APPLIED)).toEqual({
      apps: ['jira-1', 'alpha'],
      kb: [],
      recordGroups: ['group-1'],
      records: ['folder-1', 'file-1'],
    });
  });

  it('keeps a collection that was sent under apps', () => {
    expect(filtersFromAppliedFilters({ apps: [node('alpha', 'GS-Alpha', 'app', 'KB')], kb: [] }).apps).toEqual([
      'alpha',
    ]);
  });

  it('moves a collection an older message stored under kb to apps', () => {
    const legacy: AppliedFilters = {
      apps: [node('jira-1', 'Jira', 'app', 'Jira')],
      kb: [node('alpha', 'GS-Alpha', 'app', 'KB')],
    };
    expect(filtersFromAppliedFilters(legacy)).toEqual({
      apps: ['jira-1', 'alpha'],
      kb: [],
      recordGroups: [],
      records: [],
    });
  });

  it('brings back a record that was selected alone, as such', () => {
    const applied: AppliedFilters = { apps: [], kb: [], recordsExact: [node('file-1', 'a1-1', 'record', 'KB')] };
    expect(filtersFromAppliedFilters(applied)).toEqual({
      apps: [], kb: [], recordGroups: [], records: [], recordsExact: ['file-1'],
    });
    restoreAppliedFilters(applied, false);
    const store = useChatStore.getState();
    expect(store.settings.filters.recordsExact).toEqual(['file-1']);
    expect(store.collectionNamesCache['file-1']).toBe('a1-1');
  });

  it('drops the single Collections app that older chats stored', () => {
    const legacy: AppliedFilters = { apps: [node('knowledgeBase_org-1', 'Collections', 'app', 'KB')], kb: [] };
    expect(filtersFromAppliedFilters(legacy).apps).toEqual([]);
  });
});

describe('restoreAppliedFilters', () => {
  it('sets the composer selection and the names its pills need', () => {
    restoreAppliedFilters(APPLIED, false);
    const store = useChatStore.getState();
    expect(store.settings.filters).toEqual({
      apps: ['jira-1', 'alpha'],
      kb: [],
      recordGroups: ['group-1'],
      records: ['folder-1', 'file-1'],
    });
    expect(store.collectionNamesCache['folder-1']).toBe('A');
    expect(store.collectionMetaCache['file-1']).toEqual({ name: 'b1.md', nodeType: 'record', connector: 'KB' });
    expect(store.agentKnowledgeScope).toBeNull();
  });

  it('narrows a saved-agent chat to its apps and collections, kept apart', () => {
    const applied: AppliedFilters = {
      apps: [node('jira-1', 'Jira', 'app', 'Jira')],
      kb: [node('alpha', 'GS-Alpha', 'app', 'KB')],
    };
    restoreAppliedFilters(applied, true);
    const store = useChatStore.getState();
    expect(store.agentKnowledgeScope).toEqual({ apps: ['jira-1'], kb: ['alpha'] });
    expect(store.settings.filters).toEqual({ apps: [], kb: [] });
  });
});

describe('appliedFiltersFromNodes', () => {
  it('puts each node under the key its type is sent with', () => {
    const nodes = [
      node('alpha', 'GS-Alpha', 'app', 'KB'),
      node('group-1', 'Project PT', 'recordGroup', 'Jira'),
      node('folder-1', 'A', 'folder', 'KB'),
      node('file-1', 'b1.md', 'record', 'KB'),
    ];
    expect(filtersFromAppliedFilters(appliedFiltersFromNodes(nodes))).toEqual({
      apps: ['alpha'],
      kb: [],
      recordGroups: ['group-1'],
      records: ['folder-1', 'file-1'],
    });
  });
});
